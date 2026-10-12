#!/usr/bin/env python3
"""Publish fixed diagnostic categories, never fragments of private logs."""
import argparse
import json
from pathlib import Path
import re


def summarize(stage, exit_code, private_log, result=None):
    categories = []
    for category, patterns in {
        'http_timeout': ('TimeoutError', 'timed out'),
        'database_error': ('OperationalError', 'DatabaseError', 'remaining connection slots'),
        'memory_limit': ('MemoryError', 'out of memory', 'OOMKilled'),
        'host_headroom': ('host-memory-headroom-below-2GiB',),
        'image_build': ('failed to solve', 'failed to resolve', 'manifest unknown'),
        'assertion_or_guard': ('AssertionError', 'CommandError', 'ValueError'),
    }.items():
        if any(pattern in private_log for pattern in patterns):
            categories.append(category)
    failed = []
    if isinstance(result, dict) and isinstance(result.get('checks'), dict):
        failed = sorted(key for key, value in result['checks'].items()
                        if value is not True and re.fullmatch('[a-z][a-z0-9_]{0,80}', key))
    return {'stage': stage if re.fullmatch('[a-z0-9-]{1,80}', stage) else 'unknown',
            'exit_code': int(exit_code), 'error_categories': sorted(categories),
            'failed_checks': failed, 'raw_logs_private': True,
            'production_cutover_performed': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private', required=True)
    parser.add_argument('--public', required=True)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--exit-code', type=int, required=True)
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,80}', args.stage):
        raise ValueError('Invalid diagnostic stage')
    private, public = Path(args.private), Path(args.public)
    log = private / (args.stage + '.log')
    # Bounded tail is inspected privately; none of its text reaches the report.
    text = ''
    if log.is_file():
        with log.open('rb') as source:
            source.seek(max(0, log.stat().st_size-1024*1024))
            text = source.read().decode('utf-8', errors='replace')
    abort = private / 'abort-reason.txt'
    if abort.is_file():
        text += abort.read_text()
    result_path = public / (args.stage.removesuffix('-http') + '.json')
    result = json.loads(result_path.read_text()) if result_path.is_file() else None
    output = public / 'failure-diagnostic.json'
    output.write_text(json.dumps(summarize(args.stage, args.exit_code, text, result), indent=2))
    output.chmod(0o644)


if __name__ == '__main__':
    main()
