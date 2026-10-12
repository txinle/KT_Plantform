#!/usr/bin/env python3
"""Bounded architecture diagnostics and 180-client acceptance using one workload."""
import argparse
import json
import re
import time
from pathlib import Path

from school_synchronized_180 import Synchronized180Runner, StudentState, build_result, run_synchronized
from capture_protocol import BROWSER_ALIGNED_WORKLOAD, CONTRACT, evidence_files


def validate_profile(fixture, mode):
    counts = {'diagnostic': (1, 3, 10, 50, 180), 'smoke': (3, 10), 'formal': (180,)}
    n = fixture.get('student_count')
    usernames = fixture.get('usernames', [])
    source = fixture.get('source_config', {})
    if (mode not in counts or n not in counts[mode] or fixture.get('database') != 'kt_e2e'
            or fixture.get('capture_protocol') != CONTRACT or fixture.get('item_count') != 20
            or len(usernames) != n or len(set(usernames)) != n
            or any(not isinstance(name, str) or not name for name in usernames)
            or not re.fullmatch(r'/static/spa-assets/capture-shell-[a-f0-9]{20}\.html', source.get('shell_url', ''))
            or any(not re.fullmatch('[a-f0-9]{64}', source.get(key, ''))
                   for key in ('source_sha256', 'frontend_sha256', 'config_sha256'))):
        raise ValueError('A fresh, bound kt_e2e fixture matching the fixed load profile is required')
    return {'mode': mode, 'student_count': n, 'formal_capacity_accepted': False,
            'internal_budget_seconds': 240 if mode == 'diagnostic' else 1750,
            'hard_timeout_seconds': 300 if mode == 'diagnostic' else 1800,
            'request_timeout_seconds': 35, 'item_interval_seconds': 18}


def main():
    import resource

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('diagnostic', 'smoke', 'formal'), required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    args = parser.parse_args()
    fixture = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    try:
        profile = validate_profile(fixture, args.mode)
    except ValueError as error:
        parser.error(str(error))
    runner = Synchronized180Runner(args.base_url, fixture['task_id'], 35, item_interval=18,
        workload_version=BROWSER_ALIGNED_WORKLOAD, atomic_submission='auto',
        internal_budget_seconds=profile['internal_budget_seconds'])
    runner.protocol_course_id, runner.protocol_experiment_id = fixture['course_id'], fixture['experiment_id']
    runner.require_student_slice_capabilities = True
    runner.browser_shell_url = fixture['source_config']['shell_url']
    states = [StudentState(name, fixture['password']) for name in fixture['usernames']]
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.time()
    with evidence_files(args.manifest, args.output) as files:
        files['runner'] = runner
        runner.enable_attempt_journal(files['attempts'])
        run_synchronized(runner, states)
        result = build_result(runner, states, started, time.time(), expected_students=profile['student_count'])
        result.update(profile)
        result['mode'] = 'architecture_' + args.mode
        result['source_config'] = fixture['source_config']
        result['request_attempts_file'] = args.output.with_suffix('.attempts.jsonl').name
        after = resource.getrusage(resource.RUSAGE_SELF)
        result['generator'].update(cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
            max_rss_bytes=after.ru_maxrss * 1024, same_host_as_service=True,
            cpu_max=Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
            memory_max=Path('/sys/fs/cgroup/memory.max').read_text().strip())
        if args.mode != 'formal':
            result['checks'] = {key.replace('all_180_', 'all_fixture_'): value for key, value in result['checks'].items()}
            for key in ('login_load_achieved_180', 'start_load_achieved_180', 'capacity_start_ready_180'):
                result.pop(key, None)
        files['complete'] = result['workflow_completed']
        json.dump(result, files['result'], indent=2)
    print(json.dumps({key: result[key] for key in ('mode', 'student_count', 'workflow_completed', 'performance_passed', 'budget_interrupted')}))
    # A diagnostic budget cutoff is recorded as incomplete evidence, never a capacity pass.
    raise SystemExit(0 if result['workflow_completed'] else 1)


if __name__ == '__main__':
    main()
