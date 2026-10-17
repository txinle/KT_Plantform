"""Django database sessions with bounded, conditional student idle renewal."""
import math
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.sessions.backends.db import SessionStore as DatabaseSessionStore
from django.utils import timezone


IDLE_WINDOW_SECONDS = 30


class SessionStore(DatabaseSessionStore):
    # Keep this class name: Django's signing salt includes the class qualname.
    student_idle_timeout = None
    student_activity_at = None
    _loaded_expire_date = None

    def _get_session_from_db(self):
        row = super()._get_session_from_db()
        self._loaded_expire_date = row.expire_date if row else None
        return row

    def _idle_expiry(self, timeout):
        activity = self.student_activity_at or timezone.now()
        boundary = math.ceil(activity.timestamp() / IDLE_WINDOW_SECONDS) * IDLE_WINDOW_SECONDS
        return datetime.fromtimestamp(boundary, dt_timezone.utc) + timedelta(seconds=timeout)

    def get_expiry_date(self, **kwargs):
        if self.student_idle_timeout is not None:
            return self._idle_expiry(self.student_idle_timeout)
        return super().get_expiry_date(**kwargs)

    def create_model_instance(self, data):
        row = super().create_model_instance(data)
        self._loaded_expire_date = row.expire_date
        return row

    def touch_idle(self, timeout):
        now = timezone.now()
        target = self._idle_expiry(timeout)
        expiry = self._loaded_expire_date
        if not self.session_key or expiry is None or expiry <= now:
            return -2, False
        renewed = False
        if expiry < target:
            renewed = bool(self.model.objects.filter(
                session_key=self.session_key, expire_date__gt=now, expire_date__lt=target,
            ).update(expire_date=target))
            if renewed:
                expiry = target
            else:
                # A competing renewal, logout, or expiry won the conditional write.
                expiry = self.model.objects.filter(
                    session_key=self.session_key, expire_date__gt=now,
                ).values_list('expire_date', flat=True).first()
                if expiry is None:
                    return -2, False
            self._loaded_expire_date = expiry
        return math.ceil((expiry - now).total_seconds() * 1000), renewed

    def flush(self):
        super().flush()
        self.student_idle_timeout = None
        self.student_activity_at = None
        self._loaded_expire_date = None
