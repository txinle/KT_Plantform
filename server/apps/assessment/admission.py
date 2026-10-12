"""Redis-backed, task-scoped admission for bursty student entry traffic."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from typing import Any

from django.conf import settings


class AdmissionUnavailable(RuntimeError):
    pass


class AdmissionInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class AdmissionResult:
    token: str
    status: str
    retry_after: int


_REQUEST_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then
  local token_key = KEYS[4] .. existing
  local state = redis.call('HGET', token_key, 'state')
  local lease_until = tonumber(redis.call('HGET', token_key, 'lease_until') or '0')
  if state == 'queued' or (state == 'admitted' and lease_until > tonumber(ARGV[6])) then
    return existing
  end
  redis.call('ZREM', KEYS[2], existing)
  redis.call('ZREM', KEYS[3], existing)
  if state == 'admitted' then redis.call('HSET', token_key, 'state', 'expired') end
end
local token = ARGV[4]
local token_key = KEYS[4] .. token
local sequence = redis.call('INCR', KEYS[5])
redis.call('HSET', token_key, 'user_id', ARGV[1], 'task_id', ARGV[2],
  'action', ARGV[3], 'state', 'queued', 'created_at', ARGV[6])
redis.call('EXPIRE', token_key, tonumber(ARGV[5]))
redis.call('SET', KEYS[1], token, 'EX', tonumber(ARGV[5]))
redis.call('ZADD', KEYS[2], sequence, token)
return token
"""


_PROMOTE_SCRIPT = """
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
for _, token in ipairs(redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)) do
  if redis.call('EXISTS', KEYS[3] .. token) == 1 then
    redis.call('HSET', KEYS[3] .. token, 'state', 'expired')
  end
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local active = redis.call('ZCARD', KEYS[2])
for _, token in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
  if active >= capacity then break end
  local token_key = KEYS[3] .. token
  if redis.call('HGET', token_key, 'state') == 'queued' then
    local lease_until = now + tonumber(ARGV[3])
    redis.call('HSET', token_key, 'state', 'admitted', 'lease_until', lease_until)
    redis.call('ZREM', KEYS[1], token)
    redis.call('ZADD', KEYS[2], lease_until, token)
    active = active + 1
  else
    redis.call('ZREM', KEYS[1], token)
  end
end
return active
"""

_CONSUME_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state')
if state ~= 'admitted' then return 0 end
local lease_until = tonumber(redis.call('HGET', KEYS[1], 'lease_until') or '0')
if lease_until <= tonumber(ARGV[6]) then
  redis.call('HSET', KEYS[1], 'state', 'expired')
  redis.call('ZREM', KEYS[2], ARGV[4])
  return 0
end
if redis.call('HGET', KEYS[1], 'user_id') ~= ARGV[1] then return -1 end
if redis.call('HGET', KEYS[1], 'task_id') ~= ARGV[2] then return -1 end
if redis.call('HGET', KEYS[1], 'action') ~= ARGV[3] then return -1 end
redis.call('HSET', KEYS[1], 'state', 'consumed')
redis.call('ZREM', KEYS[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
return 1
"""


class AdmissionCoordinator:
    def __init__(self, client: Any = None):
        self._client_instance = client

    @property
    def client(self):
        if self._client_instance is None:
            try:
                import redis

                self._client_instance = redis.Redis.from_url(
                    settings.ASSESSMENT_ADMISSION_REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
            except Exception as exc:  # pragma: no cover - import/runtime boundary
                raise AdmissionUnavailable from exc
        return self._client_instance

    @staticmethod
    def enabled_for(task_id: int) -> bool:
        return bool(
            getattr(settings, 'ASSESSMENT_ADMISSION_ENABLED', False)
            and (str(task_id) in getattr(settings, 'ASSESSMENT_ADMISSION_TASK_IDS', frozenset()))
        )

    def _keys(self, task_id: int):
        prefix = f'assessment:admission:{task_id}'
        return f'{prefix}:queue', f'{prefix}:active', f'{prefix}:token:'

    def _promote(self, task_id: int) -> None:
        queue_key, active_key, token_prefix = self._keys(task_id)
        try:
            self.client.eval(
                _PROMOTE_SCRIPT,
                3,
                queue_key,
                active_key,
                token_prefix,
                str(time.time()),
                str(settings.ASSESSMENT_ADMISSION_CAPACITY),
                str(settings.ASSESSMENT_ADMISSION_LEASE_SECONDS),
            )
        except Exception as exc:
            raise AdmissionUnavailable from exc

    def request(self, *, task_id: int, user_id: int, action: str) -> AdmissionResult:
        queue_key, active_key, token_prefix = self._keys(task_id)
        request_key = f'{queue_key}:request:{user_id}:{action}'
        try:
            token = str(self.client.eval(
                _REQUEST_SCRIPT, 5, request_key, queue_key, active_key, token_prefix,
                f'{queue_key}:sequence', str(user_id), str(task_id), action,
                secrets.token_urlsafe(32), str(settings.ASSESSMENT_ADMISSION_TTL_SECONDS), str(time.time()),
            ))
            self._promote(task_id)
            state = self.client.hget(f'{token_prefix}{token}', 'state')
            if state not in {'queued', 'admitted'}:
                raise AdmissionUnavailable
            return AdmissionResult(
                token=token,
                status=state,
                retry_after=3 if state == 'queued' else 0,
            )
        except AdmissionUnavailable:
            raise
        except Exception as exc:
            raise AdmissionUnavailable from exc

    def status(self, *, task_id: int, user_id: int, action: str, token: str) -> AdmissionResult:
        _, _, token_prefix = self._keys(task_id)
        try:
            token_key = f'{token_prefix}{token}'
            values = self.client.hmget(token_key, ['user_id', 'task_id', 'action', 'state'])
            if values[0] != str(user_id) or values[1] != str(task_id) or values[2] != action:
                raise AdmissionInvalid
            self._promote(task_id)
            state = self.client.hget(token_key, 'state')
            if state not in {'queued', 'admitted'}:
                raise AdmissionInvalid
            return AdmissionResult(token=token, status=state, retry_after=3 if state == 'queued' else 0)
        except AdmissionInvalid:
            raise
        except Exception as exc:
            raise AdmissionUnavailable from exc

    def consume(self, *, task_id: int, user_id: int, action: str, token: str) -> None:
        _, active_key, token_prefix = self._keys(task_id)
        try:
            result = self.client.eval(
                _CONSUME_SCRIPT,
                2,
                f'{token_prefix}{token}',
                active_key,
                str(user_id),
                str(task_id),
                action,
                token,
                str(settings.ASSESSMENT_ADMISSION_TTL_SECONDS),
                str(time.time()),
            )
            if result == -1:
                raise AdmissionInvalid
            if result != 1:
                raise AdmissionInvalid
            self._promote(task_id)
        except AdmissionInvalid:
            raise
        except Exception as exc:
            raise AdmissionUnavailable from exc


def admission_result_payload(result: AdmissionResult, *, poll_url: str) -> dict[str, Any]:
    return {
        'status': result.status,
        'admission_token': result.token,
        'poll_url': poll_url,
        'retry_after_seconds': result.retry_after or 3,
    }
