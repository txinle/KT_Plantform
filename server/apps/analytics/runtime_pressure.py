"""Read a root-controlled pressure signal without database or request writes."""
import json
import math
import os
import threading
import time
from pathlib import Path


_NORMAL = {'mode': 'normal', 'expires_at': None, 'retry_after_seconds': 5}
_MODES = {'normal', 'capture_priority', 'capture_emergency', 'recovering'}
_CACHE_SECONDS = 2
_cache_lock = threading.Lock()
_cache_path = None
_cache_checked_at = float('-inf')
_cache_value = _NORMAL


def _read_pressure(path):
    try:
        with Path(path).open(encoding='utf-8') as source:
            content = source.read(4097)
        if len(content) > 4096:
            return _NORMAL
        value = json.loads(content)
        if not isinstance(value, dict) or value.get('mode') not in _MODES:
            return _NORMAL
        expires = value.get('expires_at')
        retry = value.get('retry_after_seconds')
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            return _NORMAL
        if isinstance(retry, bool) or not isinstance(retry, int) or not 5 <= retry <= 30:
            return _NORMAL
        return {'mode': value['mode'], 'expires_at': expires, 'retry_after_seconds': retry}
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # A missing, partial or invalid signal cannot strand persistent jobs.
        return _NORMAL


def runtime_pressure():
    """Return a copy of the signal, with at most one file read every two seconds."""
    global _cache_path, _cache_checked_at, _cache_value
    path = os.environ.get('SCHOOL_RUNTIME_PRESSURE_FILE', '')
    if not path:
        return dict(_NORMAL)
    monotonic = time.monotonic()
    with _cache_lock:
        if path != _cache_path or monotonic - _cache_checked_at >= _CACHE_SECONDS or monotonic < _cache_checked_at:
            _cache_value = _read_pressure(path)
            _cache_path = path
            _cache_checked_at = monotonic
        value = _cache_value
        if value['expires_at'] is not None and value['expires_at'] <= time.time():
            return dict(_NORMAL)
        return dict(value)
