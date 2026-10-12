#!/usr/bin/env python3
"""Run all six isolated diagnostic stages; failures are evidence, not promotion."""
import argparse
import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path


STAGES = (('short-3', 3, 0), ('short-50', 50, 0), ('short-150', 150, 0),
          ('short-300', 300, 0), ('baseline-300', 300, 1800), ('mixed-300', 300, 1800))
STEPS = ('setup', 'readiness', 'load', 'collect', 'reconcile')
CLEANUP_TIMEOUTS = {'collect': 120, 'reconcile': 300}


class SafetyInterrupted(RuntimeError):
    """Isolation, production protection or fixed source binding was lost."""


def write_once(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


def validate_manifest(manifest):
    if manifest.get('database') != 'kt_e2e' or not re.fullmatch(r'[A-Za-z0-9_-]+', manifest.get('run_id', '')):
        raise ValueError('Isolated kt_e2e run manifest required')
    binding = manifest.get('binding', {})
    if any(not re.fullmatch(r'[a-f0-9]{64}', binding.get(key, ''))
           for key in ('source_sha256', 'frontend_sha256', 'config_sha256')):
        raise ValueError('Fixed source/frontend/config binding required')
    stages = manifest.get('stages', [])
    if [(s.get('name'), s.get('student_count'), s.get('duration_seconds')) for s in stages] != list(STAGES):
        raise ValueError('Exactly six ordered diagnostic stages required')
    if len({s.get('fixture_id') for s in stages}) != len(STAGES):
        raise ValueError('Every stage requires a fresh independent fixture')
    for stage in stages:
        maximum = 2700 if stage['duration_seconds'] else 1800
        if not 0 < stage.get('timeout_seconds', 0) <= maximum:
            raise ValueError('Diagnostic stage timeout exceeds fixed bound')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', stage.get('fixture_id', '')) or not stage.get('cwd'):
            raise ValueError('Fixed fixture and command directory required')
        commands = stage.get('commands', {})
        if any(not isinstance(commands.get(step), list) or not commands[step]
               or not all(isinstance(arg, str) for arg in commands[step]) for step in STEPS):
            raise ValueError('Each stage requires setup/readiness/load/collect/reconcile argument vectors')


def verify_bound_files(manifest):
    for filename, expected in manifest['binding'].get('files', {}).items():
        path = Path(filename)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SafetyInterrupted('Bound source/frontend/config file changed')


def subprocess_executor(stage, step, argv, attempt_dir, timeout_seconds):
    """Private logs plus explicit JSON checks; process exit alone is not a pass."""
    with (attempt_dir / f'{step}.stdout.log').open('x', encoding='utf-8') as stdout, \
         (attempt_dir / f'{step}.stderr.log').open('x', encoding='utf-8') as stderr:
        for path in (attempt_dir / f'{step}.stdout.log', attempt_dir / f'{step}.stderr.log'):
            os.chmod(path, 0o600)
        options = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(argv, cwd=stage['cwd'], stdout=stdout, stderr=stderr, **options)
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
            else:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
                process.wait(timeout=5)
            raise
    result = {'returncode': returncode}
    if returncode == 75:
        result['safety_interrupted'] = True
    report_path = attempt_dir / f'{step}-result.json'
    if report_path.exists():
        try:
            evidence = json.loads(report_path.read_text(encoding='utf-8'))
            for key in ('performance_passed', 'data_integrity_passed', 'workflow_completed', 'safety_interrupted', 'load_executed'):
                if key in evidence:
                    result[key] = evidence[key]
        except (OSError, ValueError):
            result['evidence_invalid'] = True
    return result


def run_sweep(manifest, evidence_dir, executor=subprocess_executor, *, safety_check=None):
    """Resume terminal stages, create new fixtures for interrupted/unfinished attempts."""
    validate_manifest(manifest)
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
            raise ValueError('Diagnostic manifest or source binding changed on resume')
    else:
        write_once(manifest_path, manifest)
    rows = []
    for original in manifest['stages']:
        stage_root = root / original['name']
        stage_root.mkdir(exist_ok=True)
        attempts = sorted(stage_root.glob('attempt-*'))
        previous = None
        if attempts:
            result_path = attempts[-1] / 'result.json'
            if result_path.exists():
                previous = json.loads(result_path.read_text(encoding='utf-8'))
                if previous.get('binding') != manifest['binding']:
                    raise ValueError('Existing diagnostic evidence binding differs from manifest')
        if previous and previous.get('stage_terminal') is True and not previous.get('safety_interrupted'):
            rows.append(previous)
            continue
        attempt = len(attempts) + 1
        attempt_dir = stage_root / f'attempt-{attempt:04d}'
        attempt_dir.mkdir()
        os.chmod(attempt_dir, 0o700)
        stage = copy.deepcopy(original)
        stage['fixture_id'] = f"{manifest['run_id']}-{original['fixture_id']}-attempt-{attempt:04d}"
        write_once(attempt_dir / 'started.json', {'binding': manifest['binding'], 'stage': stage,
                                                'attempt': attempt, 'started_at': time.time()})
        row = {'name': stage['name'], 'fixture_id': stage['fixture_id'], 'attempt': attempt,
               'binding': manifest['binding'], 'started_at': time.time(), 'steps': {},
               'execution_completed': False, 'workflow_completed': False,
               'stage_terminal': False, 'load_executed': False,
               'performance_passed': False, 'data_integrity_passed': False,
               'safety_interrupted': False, 'formal_capacity_accepted': False}
        deadline = time.monotonic() + stage['timeout_seconds']
        for step in STEPS:
            invoked = False
            try:
                verify_bound_files(manifest)
                if safety_check is not None:
                    safety_check(stage, step, attempt_dir)
                argv = [arg.replace('{attempt_dir}', str(attempt_dir)).replace('{fixture_id}', stage['fixture_id'])
                        for arg in stage['commands'][step]]
                if row['safety_interrupted'] and step not in CLEANUP_TIMEOUTS:
                    result = {'returncode': None, 'skipped': True, 'reason': 'safety_interrupted'}
                elif step in ('readiness', 'load') and any(row['steps'].get(prior, {}).get('returncode') != 0
                        for prior in ('setup',) + (('readiness',) if step == 'load' else ())):
                    result = {'returncode': None, 'skipped': True, 'reason': 'setup_or_readiness_failed'}
                else:
                    remaining = CLEANUP_TIMEOUTS.get(step, deadline - time.monotonic())
                    try:
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, stage['timeout_seconds'])
                        invoked = True
                        result = executor(stage, step, argv, attempt_dir, remaining)
                    except subprocess.TimeoutExpired:
                        result = {'returncode': None, 'timed_out': True}
                    except SafetyInterrupted:
                        raise
                    except Exception as error:
                        result = {'returncode': None, 'error_type': type(error).__name__}
            except SafetyInterrupted as error:
                result = {'returncode': 75, 'safety_interrupted': True, 'error_type': type(error).__name__}
            row['steps'][step] = result
            if step == 'load':
                row['load_executed'] = result.get('load_executed', invoked) is True
            write_once(attempt_dir / f'{step}-execution.json', result)
            if result.get('safety_interrupted') is True or result.get('returncode') == 75:
                row['safety_interrupted'] = True
                row['interruption_type'] = result.get('error_type', 'SafetyInterrupted')
        row['stage_terminal'] = row['load_executed'] and not row['safety_interrupted']
        row['execution_completed'] = row['stage_terminal']
        if not row['safety_interrupted']:
            load = row['steps'].get('load', {})
            reconcile = row['steps'].get('reconcile', {})
            row['workflow_completed'] = load.get('workflow_completed') is True
            final_performance = row['steps']['collect'].get('performance_passed', load.get('performance_passed'))
            row['performance_passed'] = (final_performance is True
                and all(row['steps'][key].get('returncode') == 0 for key in ('setup', 'readiness', 'load', 'collect')))
            row['data_integrity_passed'] = reconcile.get('returncode') == 0 and reconcile.get('data_integrity_passed') is True
        row['ended_at'] = time.time()
        write_once(attempt_dir / 'result.json', row)
        rows.append(row)
        if row['safety_interrupted']:
            break
    complete = len(rows) == len(STAGES) and all(row['execution_completed'] for row in rows)
    report = {'run_id': manifest['run_id'], 'binding': manifest['binding'], 'stages': rows,
              'execution_completed': complete,
              'workflow_completed': complete and all(row['workflow_completed'] for row in rows),
              'performance_passed': complete and all(row['performance_passed'] for row in rows),
              'data_integrity_passed': complete and all(row['data_integrity_passed'] for row in rows),
              'safety_interrupted': any(row['safety_interrupted'] for row in rows),
              'formal_capacity_accepted': False}
    write_once(root / f'sweep-{uuid.uuid4().hex}.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    report = run_sweep(json.loads(Path(args.manifest).read_text(encoding='utf-8-sig')), args.evidence_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(75 if report['safety_interrupted'] else
                     0 if report['execution_completed'] and report['performance_passed'] and report['data_integrity_passed'] else 1)


if __name__ == '__main__':
    main()
