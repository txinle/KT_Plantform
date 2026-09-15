import math
import os
from pathlib import Path


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
