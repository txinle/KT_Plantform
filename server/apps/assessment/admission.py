"""Bounded direct startup execution and the unchanged legacy task admission."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any

from django.conf import settings
from django.db import connection
from config.capture_timing import pending_timing


logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _process_redis_client(url, pid):
    import redis
    return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5)


class AdmissionUnavailable(RuntimeError):
    pass


class AdmissionInvalid(RuntimeError):
    pass


@dataclass(frozen=True)
class AdmissionResult:
    token: str
    status: str
    retry_after: int
    execution_claimed: bool = False
    execution_id: str | None = None
    lease_until: float | None = None


@dataclass(frozen=True)
class AdmissionExecution:
    task_id: str
    user_id: str
    action: str
    token: str
    execution_id: str
    recovery_due: bool = False


_EXECUTING_PAGE_SCRIPT = """
local prefix = KEYS[1]
local offset, count, grace = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local rows = {}
for _, token in ipairs(redis.call('ZRANGE', prefix .. ':executing', offset, offset + count - 1)) do
  local key = prefix .. ':token:' .. token
  local values = redis.call('HMGET', key, 'task_id', 'user_id', 'action', 'execution_id', 'state', 'lease_until')
  if values[5] == 'executing' then
    table.insert(rows, {task_id=values[1], user_id=values[2], action=values[3], token=token,
      execution_id=values[4], recovery_due=tonumber(values[6] or '0') + grace <= now})
  end
end
local next_cursor = offset + count
if next_cursor >= redis.call('ZCARD', prefix .. ':executing') then next_cursor = 0 end
return cjson.encode({cursor=next_cursor, rows=rows})
"""


# Acquisition and rate charging happen only while this request starts execution.
_GLOBAL_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local op, task, user, action, token = ARGV[1], ARGV[2], ARGV[3], ARGV[4], ARGV[5]
local ttl, lease, capacity, rate = tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8]), tonumber(ARGV[9])
local prefix = KEYS[1]
local executing, bucket = prefix .. ':executing', prefix .. ':bucket'
local token_prefix = prefix .. ':token:'
local binding = prefix .. ':request:' .. task .. ':' .. user .. ':' .. action
local claimed = false
if op == 'request' then
  local existing = redis.call('GET', binding)
  local state = existing and redis.call('HGET', token_prefix .. existing, 'state')
  if state == 'executing' or state == 'succeeded' then
    -- Only this still-running server call can replay its lost EVAL response.
    claimed = state == 'executing' and existing == token and redis.call('HGET', token_prefix .. existing, 'execution_id') == ARGV[11]
    token = existing
  else
    local last = tonumber(redis.call('HGET', bucket, 'at') or now)
    local credit = math.min(1, tonumber(redis.call('HGET', bucket, 'credit') or '1') + math.max(0, now - last) * rate)
    if redis.call('ZCARD', executing) >= capacity or credit < 1 then
      return cjson.encode({token='', status='busy', retry_after=1})
    end
    redis.call('HSET', bucket, 'at', now, 'credit', credit - 1)
    redis.call('HSET', token_prefix .. token, 'user_id', user, 'task_id', task, 'action', action,
      'state', 'executing', 'requested_at', now, 'execution_started_at', now,
      'execution_id', ARGV[11], 'lease_until', now + lease)
    redis.call('SET', binding, token)
    redis.call('ZADD', executing, now, token)
    claimed = true
  end
else
  local key = token_prefix .. token
  if redis.call('HGET', key, 'user_id') ~= user or redis.call('HGET', key, 'task_id') ~= task or redis.call('HGET', key, 'action') ~= action then
    return cjson.encode({invalid=true})
  end
  local state = redis.call('HGET', key, 'state')
  if op == 'release' then
    local terminal = ARGV[12]
    if redis.call('HGET', key, 'execution_id') ~= ARGV[11] or ARGV[11] == '' then
      return cjson.encode({invalid=true})
    end
    if state == 'executing' then
      redis.call('HSET', key, 'state', terminal, 'released_at', now)
      redis.call('ZREM', executing, token)
      redis.call('EXPIRE', key, ttl)
      if redis.call('GET', binding) == token then redis.call('EXPIRE', binding, ttl) end
    elseif state ~= terminal then
      return cjson.encode({invalid=true})
    end
  end
end
local key = token_prefix .. token
local state = redis.call('HGET', key, 'state')
if state ~= 'executing' and state ~= 'succeeded' and state ~= 'failed' then
  return cjson.encode({invalid=true})
end
return cjson.encode({token=token, status=state, retry_after=state == 'executing' and 1 or 0,
  execution_claimed=claimed, execution_id=redis.call('HGET', key, 'execution_id') or cjson.null,
  lease_until=tonumber(redis.call('HGET', key, 'lease_until')) or cjson.null})
"""


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
                self._client_instance = _process_redis_client(settings.ASSESSMENT_ADMISSION_REDIS_URL, os.getpid())
            except Exception as exc:  # pragma: no cover - import/runtime boundary
                raise AdmissionUnavailable from exc
        return self._client_instance

    @staticmethod
    def enabled_for(task_id: int) -> bool:
        return bool(
            getattr(settings, 'ASSESSMENT_ADMISSION_ENABLED', False)
            and (getattr(settings, 'ASSESSMENT_ADMISSION_GLOBAL_ENABLED', False)
                 or str(task_id) in getattr(settings, 'ASSESSMENT_ADMISSION_TASK_IDS', frozenset()))
        )

    @staticmethod
    def global_enabled() -> bool:
        return bool(getattr(settings, 'ASSESSMENT_ADMISSION_GLOBAL_ENABLED', False))

    def _global_operation(self, op, *, task_id, user_id, action, token=None, execution_id=None, success=None):
        if connection.in_atomic_block:
            raise AdmissionUnavailable('Admission Redis operations must precede or follow database transactions')
        started = time.monotonic()
        profiled_at = time.perf_counter()
        calls = 0
        arguments = (
            _GLOBAL_SCRIPT, 1, settings.ASSESSMENT_ADMISSION_KEY_PREFIX,
            op, str(task_id), str(user_id), action, token or secrets.token_urlsafe(32),
            settings.ASSESSMENT_ADMISSION_TTL_SECONDS, settings.ASSESSMENT_ADMISSION_LEASE_SECONDS,
            settings.ASSESSMENT_ADMISSION_CAPACITY, settings.ASSESSMENT_ADMISSION_RATE_PER_SECOND,
            settings.ASSESSMENT_ADMISSION_BATCH_SIZE, execution_id or '', 'succeeded' if success else 'failed',
        )
        try:
            from redis.exceptions import TimeoutError as RedisTimeoutError
            client = self.client
            for attempt in range(2 if op == 'request' else 1):
                calls += 1
                try:
                    raw = client.eval(*arguments)
                    break
                except RedisTimeoutError:
                    if op != 'request' or attempt:
                        raise
                    logger.warning('Admission Redis request response timed out; replaying the same execution once')
            result = json.loads(raw)
            if result.get('invalid'):
                raise AdmissionInvalid
            return AdmissionResult(**result)
        except AdmissionInvalid:
            raise
        except Exception as exc:
            logger.warning('Admission Redis failure operation=%s cause=%s duration_ms=%.3f',
                           op, type(exc).__name__, (time.monotonic() - started) * 1000)
            raise AdmissionUnavailable from exc

        finally:
            state = pending_timing.get()
            if state is not None:
                metrics = state.setdefault('capture_metrics', {})
                metrics['admission_redis_call_count'] = metrics.get('admission_redis_call_count', 0) + calls
                field = 'admission_' + op + '_calls'
                metrics[field] = metrics.get(field, 0) + calls
                metrics['admission_redis_seconds'] = metrics.get('admission_redis_seconds', 0) + time.perf_counter() - profiled_at

    def executing_page(self, *, cursor: int = 0, limit: int = 64) -> tuple[int, list[AdmissionExecution]]:
        if not self.global_enabled() or connection.in_atomic_block:
            raise AdmissionUnavailable
        if cursor < 0 or not 1 <= limit <= 256:
            raise ValueError('Executing page requires a nonnegative cursor and limit 1..256')
        try:
            result = json.loads(self.client.eval(
                _EXECUTING_PAGE_SCRIPT, 1, settings.ASSESSMENT_ADMISSION_KEY_PREFIX, cursor, limit,
                settings.ASSESSMENT_ADMISSION_RECONCILIATION_GRACE_SECONDS,
            ))
            rows = result['rows']
            return result['cursor'], [AdmissionExecution(**row) for row in rows]
        except Exception as exc:
            raise AdmissionUnavailable from exc

    def occupancy(self) -> dict[str, int]:
        """Only executions consume the direct-start budget, including uncertain outcomes."""
        if not self.global_enabled() or connection.in_atomic_block:
            raise AdmissionUnavailable
        try:
            admitted, executing = self.client.eval(
                "return {0, redis.call('ZCARD', KEYS[1] .. ':executing')}",
                1, settings.ASSESSMENT_ADMISSION_KEY_PREFIX,
            )
            return {'admitted': admitted, 'executing': executing, 'occupied': admitted + executing}
        except Exception as exc:
            raise AdmissionUnavailable from exc

    def release(self, *, task_id: int, user_id: int, action: str, token: str,
                execution_id: str, success: bool) -> AdmissionResult:
        if not self.global_enabled():
            raise AdmissionInvalid
        return self._global_operation('release', task_id=task_id, user_id=user_id, action=action,
                                      token=token, execution_id=execution_id, success=success)

    def recover(self, **identity) -> AdmissionResult:
        """Release only after the caller has established a durable DB outcome.

        This deliberately has no timeout-based recovery: an executing request may
        still own a database transaction after its HTTP worker disappears.
        """
        return self.release(**identity)

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
        if self.global_enabled():
            return self._global_operation('request', task_id=task_id, user_id=user_id, action=action, execution_id=str(uuid.uuid4()))
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
        if self.global_enabled():
            return self._global_operation('status', task_id=task_id, user_id=user_id, action=action, token=token)
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
        if self.global_enabled():
            # A global execution needs a fencing id and durable outcome; legacy
            # consume must not bypass that lifecycle.
            raise AdmissionInvalid
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
