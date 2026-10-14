"""School candidate sessions: signed Django data, atomic create/update with TTL."""
from functools import lru_cache

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from django.conf import settings
from django.contrib.sessions.backends.base import CreateError, SessionBase, UpdateError


STUDENT_IDLE_GRACE_SECONDS = 30
TOUCH_IDLE_SCRIPT = '''
local ttl = redis.call('PTTL', KEYS[1])
if ttl == -2 then return {ttl, 0} end
if ttl <= tonumber(ARGV[1]) then
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
    return {tonumber(ARGV[2]), 1}
end
return {ttl, 0}
'''


class SessionStoreUnavailable(Exception):
    def __init__(self, operation):
        self.operation = operation
        super().__init__('Session store unavailable')


@lru_cache(maxsize=8)
def session_client(url, timeout):
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=timeout,
                               socket_connect_timeout=timeout, retry=Retry(NoBackoff(), 0))


class SessionStore(SessionBase):
    student_idle_timeout = None

    def flush(self):
        super().flush()
        self.student_idle_timeout = None

    def get_expiry_age(self, **kwargs):
        if self.student_idle_timeout is not None:
            return self.student_idle_timeout + STUDENT_IDLE_GRACE_SECONDS
        return super().get_expiry_age(**kwargs)

    def touch_idle(self, timeout):
        if not self.session_key:
            return -2, False
        ttl, renewed = self._redis('eval', TOUCH_IDLE_SCRIPT, 1, self._key(self.session_key),
                                   timeout * 1000, (timeout + STUDENT_IDLE_GRACE_SECONDS) * 1000)
        return int(ttl), bool(renewed)

    def _redis(self, operation, *args, **kwargs):
        try:
            client = session_client(settings.SESSION_REDIS_URL, settings.SESSION_REDIS_TIMEOUT_SECONDS)
            return getattr(client, operation)(*args, **kwargs)
        except redis.RedisError:
            self.store_failure = SessionStoreUnavailable(operation)
            raise self.store_failure from None

    def _key(self, session_key):
        return settings.SESSION_REDIS_KEY_PREFIX + session_key

    def load(self):
        data = self._redis('get', self._key(self.session_key))
        if data is None:
            self._session_key = None
            return {}
        return self.decode(data)

    def exists(self, session_key):
        return bool(self._redis('exists', self._key(session_key)))

    def create(self):
        while True:
            self._session_key = self._get_new_session_key()
            try:
                self.save(must_create=True)
            except CreateError:
                continue
            self.modified = True
            return

    def save(self, must_create=False):
        if self.session_key is None:
            return self.create()
        data = self._get_session(no_load=must_create)
        if self.student_idle_timeout is not None:
            data['_session_expiry'] = self.get_expiry_age()
        saved = self._redis('set', self._key(self.session_key), self.encode(data),
                            ex=max(1, self.get_expiry_age()), nx=must_create, xx=not must_create)
        if not saved:
            raise CreateError if must_create else UpdateError

    def delete(self, session_key=None):
        session_key = session_key or self.session_key
        if session_key:
            self._redis('delete', self._key(session_key))

    @classmethod
    def clear_expired(cls):
        # Redis expires each key atomically; no scan or database cleanup needed.
        pass
