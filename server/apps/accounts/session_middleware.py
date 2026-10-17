"""Django's cookie policy with measured session operations and explicit outages."""
import time
import math

from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware as DjangoSessionMiddleware
from django.http import JsonResponse
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date
from django.utils import timezone

from config.capture_timing import TIMING_ENV_KEY
from .redis_sessions import SessionStore, SessionStoreUnavailable
from .database_sessions import SessionStore as DatabaseSessionStore


class SessionMiddleware(DjangoSessionMiddleware):
    def process_request(self, request):
        super().process_request(request)
        state = request.META.get(TIMING_ENV_KEY)
        if state is not None:
            state.setdefault('capture_metrics', {}).update({
                'session_backend': 'redis' if isinstance(request.session, SessionStore) else 'database',
                'session_load_count': 0, 'session_load_seconds': 0,
                'session_save_count': 0, 'session_save_seconds': 0,
                'session_touch_count': 0, 'session_touch_seconds': 0, 'session_touch_renew_count': 0,
            })
            active = set()
            for operation in ('load', 'save'):
                original = getattr(request.session, operation)
                def measured(*args, _original=original, _operation=operation, **kwargs):
                    if _operation in active:
                        return _original(*args, **kwargs)
                    active.add(_operation)
                    stamp = time.perf_counter()
                    try:
                        return _original(*args, **kwargs)
                    finally:
                        active.remove(_operation)
                        metrics = state.setdefault('capture_metrics', {})
                        field = 'session_' + _operation
                        metrics[field + '_count'] = metrics.get(field + '_count', 0) + 1
                        metrics[field + '_seconds'] = metrics.get(field + '_seconds', 0) + time.perf_counter() - stamp
                setattr(request.session, operation, measured)
        # Catch transport failure before lazy authentication inside other middleware.
        if isinstance(request.session, SessionStore) and request.session.session_key:
            try:
                request.session._get_session()
            except SessionStoreUnavailable as error:
                return self.unavailable(request, error, business_outcome_unknown=False)

    def process_exception(self, request, exception):
        if isinstance(exception, SessionStoreUnavailable):
            return self.unavailable(request, exception, business_outcome_unknown=request.method not in {'GET', 'HEAD', 'OPTIONS'})

    def process_response(self, request, response):
        stamp = time.perf_counter()
        try:
            error = getattr(getattr(request, 'session', None), 'store_failure', None)
            if error is not None:
                return response if response.status_code == 503 else self.unavailable(request, error, business_outcome_unknown=request.method not in {'GET', 'HEAD', 'OPTIONS'})
            try:
                session = getattr(request, 'session', None)
                touch = (isinstance(session, (SessionStore, DatabaseSessionStore)) and session.student_idle_timeout is not None
                         and not session.modified and not settings.SESSION_SAVE_EVERY_REQUEST
                         and not session.is_empty() and response.status_code < 500)
                response = super().process_response(request, response)
                if touch:
                    self.touch_student_cookie(request, response, session)
                elif (isinstance(session, DatabaseSessionStore) and session.student_idle_timeout is not None
                      and (session.modified or settings.SESSION_SAVE_EVERY_REQUEST)
                      and not session.is_empty() and response.status_code < 500):
                    ttl = math.ceil((session._loaded_expire_date - timezone.now()).total_seconds() * 1000)
                    self.set_student_cookie(response, session, ttl)
                return response
            except SessionStoreUnavailable as error:
                return self.unavailable(request, error, business_outcome_unknown=request.method not in {'GET', 'HEAD', 'OPTIONS'})
        finally:
            state = request.META.get(TIMING_ENV_KEY)
            if state is not None:
                state.setdefault('capture_metrics', {})['session_response_seconds'] = time.perf_counter() - stamp

    @staticmethod
    def touch_student_cookie(request, response, session):
        stamp = time.perf_counter()
        renewed = False
        try:
            ttl, renewed = session.touch_idle(session.student_idle_timeout)
        finally:
            state = request.META.get(TIMING_ENV_KEY)
            if state is not None:
                metrics = state.setdefault('capture_metrics', {})
                metrics['session_touch_count'] = metrics.get('session_touch_count', 0) + 1
                metrics['session_touch_seconds'] = metrics.get('session_touch_seconds', 0) + time.perf_counter() - stamp
                metrics['session_touch_renew_count'] = metrics.get('session_touch_renew_count', 0) + int(renewed)
        SessionMiddleware.set_student_cookie(response, session, ttl)

    @staticmethod
    def set_student_cookie(response, session, ttl):
        if ttl <= 0:
            response.delete_cookie(settings.SESSION_COOKIE_NAME, path=settings.SESSION_COOKIE_PATH,
                                   domain=settings.SESSION_COOKIE_DOMAIN, samesite=settings.SESSION_COOKIE_SAMESITE)
        else:
            max_age = max(1, math.ceil(ttl / 1000))
            expires_at = (session._loaded_expire_date.timestamp() if isinstance(session, DatabaseSessionStore)
                          else time.time() + ttl / 1000)
            response.set_cookie(settings.SESSION_COOKIE_NAME, session.session_key,
                                max_age=max_age, expires=http_date(math.ceil(expires_at)),
                                domain=settings.SESSION_COOKIE_DOMAIN, path=settings.SESSION_COOKIE_PATH,
                                secure=settings.SESSION_COOKIE_SECURE or None,
                                httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                                samesite=settings.SESSION_COOKIE_SAMESITE)
        patch_vary_headers(response, ('Cookie',))

    @staticmethod
    def unavailable(request, error, *, business_outcome_unknown):
        return JsonResponse({'error': {
            'code': 'SESSION_STORE_UNAVAILABLE',
            'message': '会话服务暂不可用。请先确认操作状态，再重新登录。',
            'fields': {'business_outcome_unknown': business_outcome_unknown},
            'request_id': getattr(request, 'request_id', ''),
        }}, status=503, headers={'Cache-Control': 'no-store'})
