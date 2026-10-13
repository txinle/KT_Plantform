"""Django's cookie policy with measured session operations and explicit outages."""
import time

from django.contrib.sessions.middleware import SessionMiddleware as DjangoSessionMiddleware
from django.http import JsonResponse

from config.capture_timing import TIMING_ENV_KEY
from .redis_sessions import SessionStore, SessionStoreUnavailable


class SessionMiddleware(DjangoSessionMiddleware):
    def process_request(self, request):
        super().process_request(request)
        state = request.META.get(TIMING_ENV_KEY)
        if state is not None:
            state.setdefault('capture_metrics', {}).update({
                'session_backend': 'redis' if isinstance(request.session, SessionStore) else 'database',
                'session_load_count': 0, 'session_load_seconds': 0,
                'session_save_count': 0, 'session_save_seconds': 0,
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
                return super().process_response(request, response)
            except SessionStoreUnavailable as error:
                return self.unavailable(request, error, business_outcome_unknown=request.method not in {'GET', 'HEAD', 'OPTIONS'})
        finally:
            state = request.META.get(TIMING_ENV_KEY)
            if state is not None:
                state.setdefault('capture_metrics', {})['session_response_seconds'] = time.perf_counter() - stamp

    @staticmethod
    def unavailable(request, error, *, business_outcome_unknown):
        return JsonResponse({'error': {
            'code': 'SESSION_STORE_UNAVAILABLE',
            'message': '会话服务暂不可用。请先确认操作状态，再重新登录。',
            'fields': {'business_outcome_unknown': business_outcome_unknown},
            'request_id': getattr(request, 'request_id', ''),
        }}, status=503, headers={'Cache-Control': 'no-store'})
