#!/usr/bin/env python3
"""Calibrate the 180-thread sender against the gateway's existing static shell."""
import argparse
import concurrent.futures
import json
import resource
import threading
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    barrier = threading.Barrier(181)
    before = resource.getrusage(resource.RUSAGE_SELF)
    def request(index):
        opener = urllib.request.build_opener()
        barrier.wait(timeout=30)
        sent = time.perf_counter()
        status, error = None, None
        try:
            with opener.open('http://127.0.0.1:18080/static/spa-assets/index.html', timeout=35) as response:
                status = response.status
                response.read()
        except Exception as failure:
            error = type(failure).__name__
        return {'index': index, 'sent': sent, 'finished': time.perf_counter(), 'status': status, 'error_type': error}
    with concurrent.futures.ThreadPoolExecutor(max_workers=180) as pool:
        pending = [pool.submit(request, index) for index in range(180)]
        barrier.wait(timeout=30)
        rows = [future.result() for future in pending]
    after = resource.getrusage(resource.RUSAGE_SELF)
    window = max(row['sent'] for row in rows) - min(row['sent'] for row in rows)
    result = {'kind': 'static_gateway_sender_calibration', 'students': 180, 'request_count': len(rows),
        'send_window_seconds': window, 'all_http_200': all(row['status'] == 200 for row in rows),
        'same_host_as_service': True, 'formal_capacity_accepted': False,
        'measurement_boundary': 'Immediately before urllib opener.open; kernel socket transmission is not directly observed.',
        'cpu_max': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
        'memory_max': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
        'cpu_seconds': after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
        'max_rss_bytes': after.ru_maxrss * 1024, 'requests': rows}
    result['passed'] = result['all_http_200'] and window <= 1
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(result, target, indent=2)
    print(json.dumps({key: result[key] for key in ('passed', 'send_window_seconds', 'cpu_seconds', 'max_rss_bytes')}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
