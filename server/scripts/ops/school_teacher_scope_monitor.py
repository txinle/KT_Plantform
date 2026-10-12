#!/usr/bin/env python3
"""Fixed isolated v6 teachers: real scope POST through first preview response."""
import concurrent.futures
import json
import math
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

RUN = '20261010T115729Z-358195'
SHA = '79a8c019c3ae81414ea86be619343b9d5391e7ae1632ccc32e0408ab9287bfcf'
PRIVATE = Path('/home/ktdata/school-cache-private') / RUN
PUBLIC = Path('/home/ktdata/school-cache-reports') / RUN
CANDIDATE = Path('/home/ktdata/school-cache-candidate')
MANIFEST = PRIVATE / 'fixtures' / (RUN + '-v6-mixed.json')
STOP = threading.Event()


def bound_source(binding, upgrade=None):
    if binding.get('run_id') != RUN:
        raise RuntimeError('Fixed source run mismatch')
    value = binding.get('candidate_archive_sha256')
    if value == SHA:
        return value
    expected = {'run_id': RUN, 'candidate_archive_sha256': value, 'old_source_sha256': SHA,
                'archive': '/home/ktdata/school-candidate-v7.tar', 'fixture_run_id': RUN + '-v6'}
    if not re.fullmatch('[a-f0-9]{64}', value or '') or not upgrade or any(upgrade.get(key) != content for key, content in expected.items()):
        raise RuntimeError('Fixed v7 retained-fixture source binding required')
    return value


def measure_switch(client, state, prefix, selection, clock=time.perf_counter):
    started = clock()
    status, payload, _ = client.request(state, prefix + 'scope/', method='POST', body=selection)
    current = payload.get('current', {})
    if status != 200 or sorted(current.get('experiment_ids', [])) != sorted(selection['experiment_ids']) or current.get('class_id') != selection['class_id']:
        raise RuntimeError('Scope POST did not confirm requested selection')
    query = urlencode({'experiment_ids': ','.join(map(str, selection['experiment_ids'])),
                       'class_id': selection['class_id'], 'table': 'sessions', 'page': 1})
    status, _, _ = client.request(state, prefix + 'advanced/?' + query)
    if status != 200:
        raise RuntimeError('First preview response failed')
    return clock() - started


def evaluate(samples, mixed, history_ready, authentication_completed_at=0):
    rows = [row for row in samples if row['started_at'] >= mixed['started_at'] and row['ended_at'] <= mixed['ended_at']]
    successful = [row for row in rows if row['ok']]
    seconds = sorted(row['seconds'] for row in successful)
    p95 = seconds[math.ceil(len(seconds) * .95) - 1] if seconds else None
    errors = len(rows) - len(successful)
    coverage = {teacher: {row['scope_slot'] for row in successful if row['teacher_slot'] == teacher} for teacher in range(5)}
    checks = {'scope_post_and_first_preview_p95_at_most_1_5_seconds': p95 is not None and p95 <= 1.5,
        'error_rate_below_0_1_percent': bool(rows) and errors / len(rows) < .001,
        'at_least25_complete_samples': len(rows) >= 25,
        'all5_authenticated_teachers_cover5_switch_positions': all(len(scopes) == 5 for scopes in coverage.values()),
        'history_ready_before_measurement': history_ready,
        'normal_authentication_before_timed_workload': authentication_completed_at <= mixed['started_at']}
    return {'run_id': RUN, 'source_sha256': SHA, 'passed': all(checks.values()), 'checks': checks,
        'sample_count': len(rows), 'errors': errors, 'error_rate': errors / len(rows) if rows else None,
        'combined_p95_seconds': p95, 'combined_max_seconds': max(seconds) if seconds else None,
        'teacher_count': sum(bool(scopes) for scopes in coverage.values()), 'switch_positions_per_teacher': [len(coverage[index]) for index in range(5)],
        'distinct_valid_scopes': 3, 'authentication_completed_at': authentication_completed_at,
        'mixed_started_at': mixed['started_at'], 'mixed_ended_at': mixed['ended_at'],
        'excluded_outside_window_count': len(samples) - len(rows), 'samples': rows}


def save(value):
    import pwd
    path = PUBLIC / 'teacher-scope-preview.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)
    path.chmod(0o644)
    owner = pwd.getpwnam('ktdata')
    os.chown(path, owner.pw_uid, owner.pw_gid)


def state():
    value = json.loads((PUBLIC / 'status.json').read_text())
    if value.get('run_id') != RUN or value.get('state') == 'failed':
        raise RuntimeError('Fixed candidate run failed or changed')
    resource_path = PUBLIC / 'postgres-resource-profile.json'
    if resource_path.is_file():
        resource = json.loads(resource_path.read_text())
        if resource.get('run_id') != RUN or str(resource.get('state', '')).startswith('failed'):
            raise RuntimeError('Fixed candidate resource observation failed or changed')
    return value


def main():
    global SHA
    import fcntl
    if os.geteuid() != 0:
        raise RuntimeError('Authenticated sudo required')
    lock = open('/run/lock/kt-school-teacher-scope-monitor.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    binding = json.loads((PRIVATE / 'cached-source-binding.json').read_text())
    sealed = PRIVATE / 'v7-coordinator-binding.json'
    upgrade = None
    if sealed.is_file() and not sealed.is_symlink() and sealed.stat().st_uid == 0 and not sealed.stat().st_mode & 0o077:
        upgrade = json.loads(sealed.read_text())
    SHA = bound_source(binding, upgrade)
    deadline = time.monotonic() + 10800
    save({'run_id': RUN, 'source_sha256': SHA, 'state': 'waiting-mixed-preparation', 'passed': False})
    while time.monotonic() < deadline:
        stage = state().get('stage')
        if stage in ('mixed-observation', 'mixed-30-minute'):
            break
        if stage in ('observation-completion', 'formal-acceptance', 'accepted-candidate-no-promotion'):
            raise RuntimeError('Monitor missed mixed workload')
        time.sleep(.5)
    else:
        raise RuntimeError('Bounded monitor wait expired')
    if not MANIFEST.is_file() or MANIFEST.is_symlink() or MANIFEST.stat().st_uid != 0 or MANIFEST.stat().st_mode & 0o077:
        raise RuntimeError('Fixed root-private fixture manifest required')
    fixture = json.loads(MANIFEST.read_text())
    names = fixture.get('teacher_usernames', [])
    if fixture.get('database') != 'kt_e2e' or fixture.get('run_id') != RUN + '-v6-mixed' or len(names) != 5 or len(set(names)) != 5:
        raise RuntimeError('Exactly five fixed isolated test teachers required')
    sys.path.insert(0, str(CANDIDATE / 'scripts/load'))
    from assessment_300 import Runner, StudentState
    runner = Runner('http://127.0.0.1:18080', fixture['task_id'], 35)
    teachers = [StudentState(name, fixture['password']) for name in names]
    runner.phase('login', teachers, runner.login, 6, synchronized=False)
    if runner.errors:
        raise RuntimeError('Supplemental fixture authentication failed')
    authenticated_at = time.time()
    prefix = f"/api/v1/teacher/courses/{fixture['course_id']}/data/"
    history, current, group = fixture['history_experiment_id'], fixture['experiment_id'], fixture['class_id']
    # Five valid selections; class-less scope is incomplete in the real UI.
    selections = [{'experiment_ids': ids, 'class_id': group} for ids in
                  ([history], [current], [history, current], [history], [current])]
    for teacher in teachers:
        query = urlencode({'experiment_ids': history, 'class_id': group, 'table': 'sessions', 'page': 1})
        status, payload, _ = runner.request(teacher, prefix + 'advanced/?' + query)
        if status != 200 or payload.get('preparation', {}).get('state') != 'ready' or not payload.get('preview', {}).get('rows'):
            raise RuntimeError('History preview is not ready before measurement')
    samples = []
    guard = threading.Lock()
    save({'run_id': RUN, 'source_sha256': SHA, 'state': 'authenticated-waiting-mixed', 'passed': False,
          'teacher_count': 5, 'authentication_completed_at': authenticated_at})

    def teacher_work(index, teacher):
        cycle = 0
        while time.monotonic() < deadline and not STOP.is_set():
            stage = state().get('stage')
            if stage in ('observation-completion', 'formal-acceptance', 'accepted-candidate-no-promotion'):
                return
            if stage != 'mixed-30-minute':
                time.sleep(.5)
                continue
            started = time.time()
            try:
                elapsed = measure_switch(runner, teacher, prefix, selections[cycle % 5])
                ok = True
            except Exception:
                elapsed = time.time() - started
                ok = False
            with guard:
                samples.append({'teacher_slot': index, 'scope_slot': cycle % 5, 'started_at': started,
                                'ended_at': time.time(), 'seconds': elapsed, 'ok': ok})
            cycle += 1
            STOP.wait(max(0, 10 - (time.time() - started)))
        raise RuntimeError('Bounded scope observation expired or interrupted')

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(teacher_work, index, teacher) for index, teacher in enumerate(teachers)]
        for future in futures:
            future.result()
    mixed = json.loads((PUBLIC / 'mixed.json').read_text())
    result = evaluate(samples, mixed, True, authenticated_at)
    result['state'] = 'completed'
    save(result)
    print('Teacher scope/preview supplemental gate: ' + ('passed' if result['passed'] else 'failed'))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    def interrupted(signum, frame):
        STOP.set()
        raise RuntimeError('Supplemental monitor interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except Exception as error:
        save({'run_id': RUN, 'source_sha256': SHA, 'state': 'failed', 'passed': False,
              'error_type': type(error).__name__, 'at': time.time()})
        print('Teacher supplemental monitor failed: ' + type(error).__name__)
        raise SystemExit(1)
