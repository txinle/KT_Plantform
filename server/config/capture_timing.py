"""Timing boundaries for the explicitly isolated capture diagnostic run."""
import math
import os
import re
import time
from contextvars import ContextVar


TIMING_ENV_KEY = 'kt.capture_timing'
CLOCK_DISCONTINUITY_SECONDS = .05
pending_timing = ContextVar('capture_request_timing', default=None)


def diagnostics_enabled():
    if os.getenv('SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED') != '1':
        return False
    if os.getenv('POSTGRES_DB') != 'kt_e2e':
        raise ValueError('Capture diagnostics requires the isolated kt_e2e database')
    return True


def diagnostics_mode():
    mode = os.getenv('SCHOOL_CAPTURE_DIAGNOSTICS_MODE', 'sql')
    if mode not in ('light', 'sql'):
        raise ValueError('Capture diagnostics mode must be light or sql')
    return mode


def sanitized_id(value):
    return value if isinstance(value, str) and re.fullmatch('[a-f0-9]{32}', value) else None


def coarse_path(path):
    return re.sub(r'/(?:\d+|[a-fA-F0-9-]{32,36})(?=/|$)', '/:id', path.split('?', 1)[0])


def ingress_stamp(value):
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    return stamp if math.isfinite(stamp) and stamp >= 0 else None


def mark(state, phase):
    state.setdefault('marks', {})[phase] = (time.perf_counter(), time.time())


def finish_interval(detail, started_clock, state, phase):
    seconds = time.perf_counter() - started_clock
    detail['ended_at'] = time.time()
    detail['seconds'] = round(seconds, 6)
    error = detail['ended_at'] - detail['started_at'] - seconds
    detail['clock_delta_error_seconds'] = round(error, 6)
    detail['clock_discontinuity'] = abs(error) > CLOCK_DISCONTINUITY_SECONDS
    if detail['clock_discontinuity']:
        state.setdefault('clock_errors', []).append((phase, error))
    return seconds


def timing_fields(state):
    marks = state.get('marks', {})
    row = {}
    if 'diagnostics_mode' in state:
        row['diagnostics_mode'] = state['diagnostics_mode']
    row.update(state.get('capture_metrics', {}))
    for phase, field in (
        ('wsgi_start', 'wsgi_started_at'), ('middleware_start', 'middleware_started_at'),
        ('middleware_end', 'middleware_ended_at'), ('wsgi_headers', 'wsgi_headers_at'),
        ('wsgi_return', 'wsgi_returned_at'), ('wsgi_complete', 'wsgi_completed_at'),
    ):
        if phase in marks:
            row[field] = marks[phase][1]
    for first, last, field in (
        ('gunicorn_pre', 'wsgi_start', 'gunicorn_pre_to_wsgi_seconds'),
        ('wsgi_start', 'middleware_start', 'wsgi_to_middleware_seconds'),
        ('middleware_start', 'middleware_end', 'middleware_seconds'),
        ('wsgi_start', 'wsgi_headers', 'wsgi_to_headers_seconds'),
        ('wsgi_start', 'wsgi_return', 'wsgi_application_seconds'),
        ('wsgi_return', 'wsgi_complete', 'wsgi_iteration_seconds'),
    ):
        if first in marks and last in marks:
            row[field] = round(marks[last][0] - marks[first][0], 6)
    errors = list(state.get('clock_errors', []))
    stamps = list(marks.items())
    for (first, (start_clock, start_wall)), (last, (end_clock, end_wall)) in zip(stamps, stamps[1:]):
        error = end_wall - start_wall - (end_clock - start_clock)
        errors.append((f'{first}_to_{last}', error))
    discontinuities = [phase for phase, error in errors if abs(error) > CLOCK_DISCONTINUITY_SECONDS]
    row['clock_discontinuity'] = bool(discontinuities)
    row['clock_discontinuity_phases'] = discontinuities
    row['clock_delta_error_seconds'] = round(max((error for _, error in errors), key=abs, default=0), 6)
    row['clock_discontinuity_threshold_seconds'] = CLOCK_DISCONTINUITY_SECONDS
    ingress = state.get('nginx_ingress_at')
    delta = marks['gunicorn_pre'][1] - ingress if ingress is not None and 'gunicorn_pre' in marks else None
    untrusted = bool(discontinuities) or (delta is not None and delta < 0)
    row['nginx_ingress_at'] = ingress
    row['nginx_ingress_to_gunicorn_pre_wall_seconds'] = round(delta, 6) if delta is not None and not untrusted else None
    row['nginx_clock_alignment'] = 'assumed_shared_host_clock'
    row['nginx_wall_correlation_untrusted'] = untrusted
    return row


class CaptureResponse:
    def __init__(self, response, state):
        self.response = response
        self.state = state

    def __iter__(self):
        try:
            yield from self.response
        except Exception as error:
            self.state['error_type'] = type(error).__name__
            raise
        finally:
            self.finish()

    def finish(self):
        if 'wsgi_complete' not in self.state['marks']:
            mark(self.state, 'wsgi_complete')

    def close(self):
        try:
            close = getattr(self.response, 'close', None)
            if close is not None:
                close()
        finally:
            self.finish()


class WSGICaptureTiming:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        if not diagnostics_enabled():
            return self.application(environ, start_response)
        state = pending_timing.get()
        if state is None:
            state = {}
        environ[TIMING_ENV_KEY] = state
        state['diagnostics_mode'] = diagnostics_mode()
        mark(state, 'wsgi_start')
        state['nginx_ingress_at'] = ingress_stamp(environ.get('HTTP_X_REQUEST_STARTED_AT'))

        def capture_start_response(status, headers, exc_info=None):
            if 'wsgi_headers' not in state['marks']:
                mark(state, 'wsgi_headers')
            return start_response(status, headers, exc_info)

        try:
            response = self.application(environ, capture_start_response)
        except Exception as error:
            state['error_type'] = type(error).__name__
            mark(state, 'wsgi_complete')
            raise
        mark(state, 'wsgi_return')
        return CaptureResponse(response, state)
