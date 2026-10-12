import math
import os
import json
import logging
from pathlib import Path

from config.capture_timing import (
    TIMING_ENV_KEY, coarse_path, diagnostics_enabled, diagnostics_mode, ingress_stamp,
    mark, pending_timing, sanitized_id, timing_fields,
)


def _read_text(path):
    try:
        return Path(path).read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return ""


def effective_cpu_count():
    detected = max(1, os.cpu_count() or 1)
    quota = _read_text("/sys/fs/cgroup/cpu.max").split()
    if len(quota) == 2 and quota[0] != "max":
        try:
            detected = min(detected, max(1, math.ceil(int(quota[0]) / int(quota[1]))))
        except (ValueError, ZeroDivisionError):
            pass
    return detected


def effective_memory_bytes():
    cgroup_limit = _read_text("/sys/fs/cgroup/memory.max")
    if cgroup_limit and cgroup_limit != "max":
        try:
            return int(cgroup_limit)
        except ValueError:
            pass
    for line in _read_text("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                break
    return 2 * 1024**3


def server_worker_count():
    configured = os.getenv("GUNICORN_WORKERS", "auto").strip().lower()
    if configured != "auto":
        return max(1, int(configured))
    memory_per_worker = max(
        256,
        int(os.getenv("GUNICORN_MEMORY_PER_WORKER_MB", "512")),
    ) * 1024**2
    replicas_on_server = max(1, int(os.getenv("WEB_REPLICAS", "1")))
    cpu_bound = max(1, effective_cpu_count() // replicas_on_server)
    memory_bound = max(
        1,
        effective_memory_bytes() // replicas_on_server // memory_per_worker,
    )
    return max(1, min(cpu_bound, memory_bound))


bind = "0.0.0.0:8000"
workers = server_worker_count()
worker_class = "sync"
threads = 1
timeout = 60
graceful_timeout = 60
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "200"))
accesslog = "-"
errorlog = "-"
logger = logging.getLogger('kt.capture_diagnostics')


def pre_request(worker, req):
    if diagnostics_enabled():
        state = {'diagnostics_mode': diagnostics_mode()}
        mark(state, 'gunicorn_pre')
        req.capture_started_clock, req.capture_started_at = state['marks']['gunicorn_pre']
        req.capture_timing = state
        req.capture_timing_token = pending_timing.set(state)


def post_request(worker, req, environ, resp):
    if not hasattr(req, 'capture_started_clock'):
        return
    try:
        state = environ.get(TIMING_ENV_KEY, req.capture_timing)
        mark(state, 'gunicorn_end')
        headers = dict((key.lower(), value) for key, value in req.headers)
        request_id = sanitized_id(headers.get('x-request-id'))
        if request_id is None:
            return
        state.setdefault('nginx_ingress_at', ingress_stamp(headers.get('x-request-started-at')))
        ended_clock, ended_at = state['marks']['gunicorn_end']
        row = {'phase': 'gunicorn_request_complete', 'request_id': request_id,
            'operation_id': sanitized_id(headers.get('x-kt-operation-id')),
            'worker_pid': worker.pid, 'method': req.method, 'path': coarse_path(req.path),
            'started_at': req.capture_started_at, 'ended_at': ended_at,
            'seconds': round(ended_clock - req.capture_started_clock, 6),
            'error_type': state.get('error_type')}
        row.update(timing_fields(state))
        logger.warning(json.dumps(row, separators=(',', ':')))
    finally:
        pending_timing.reset(req.capture_timing_token)
        del req.capture_timing_token
        del req.capture_started_clock


def post_worker_init(worker):
    from django.urls import resolve

    # Load the route/view modules before this worker accepts student requests.
    resolve('/health/')
