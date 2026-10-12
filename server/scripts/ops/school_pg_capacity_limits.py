#!/usr/bin/env python3
"""Independent live-PG cgroup limiter; restore exact prior kernel limits on failure."""
import json
import hashlib
import atexit
import os
import signal
import subprocess
import time
from pathlib import Path


RUN_ID = '20261010T115729Z-358195'
REPORT = Path('/home/ktdata/school-cache-reports') / RUN_ID
CONTAINER = 'kt_plantform-db-1'
TARGET = {'memory.max': str(4 * 1024**3), 'memory.swap.max': '0', 'cpu.max': '300000 100000', 'cpu.weight': '120'}
ALLOWED_START = {'cached-candidate-build', 'cached-compose-validation', 'create-test-services',
    'attach-test-postgres', 'candidate-start', 'nginx-parser-check', 'verify-web', 'verify-student-read',
    'verify-teacher-read', 'pgbouncer-pools', 'capacity-preflight', 'seed-fixtures'}


def read_limits(group):
    return {name: (Path(group) / name).read_text().strip() for name in TARGET}


def restore_limits(group, limits):
    for name in ('memory.max', 'memory.swap.max', 'cpu.max', 'cpu.weight'):
        (Path(group) / name).write_text(limits[name] + '\n')
    if read_limits(group) != limits:
        raise ValueError('Kernel resource restoration did not match original values')


def nonreclaimable_memory(current, values):
    clean_inactive = max(0, min(values['inactive_file'], values['file'])
                         - values['file_dirty'] - values['file_writeback'])
    # Active cache, anonymous memory and all kernel charges retain their full
    # charge. Dirty/writeback are subtracted conservatively even if overlapping.
    return max(0, current - clean_inactive)


def memory_headroom(group):
    values = dict((key, int(value)) for key, value in
                  (line.split() for line in (Path(group) / 'memory.stat').read_text().splitlines()))
    return nonreclaimable_memory(int((Path(group) / 'memory.current').read_text()), values)


def phase_target(policy, source_sha):
    if (policy.get('run_id') != RUN_ID or policy.get('source_sha256') != source_sha
        or policy.get('phase') not in ('short-50', 'short-150', 'short-300', 'formal')
        or policy.get('postgres_memory_gib') not in (4, 5) or policy.get('postgres_cpus') != 5):
        raise ValueError('Source-bound PostgreSQL phase budget mismatch')
    if policy['phase'] == 'short-50' and policy['postgres_memory_gib'] != 4:
        raise ValueError('Initial short phase requires4GiB PostgreSQL memory')
    return {'memory.max': str(policy['postgres_memory_gib'] * 1024**3),
            'memory.swap.max': '0', 'cpu.max': '500000 100000', 'cpu.weight': '120'}


def run_target():
    private = Path('/home/ktdata/school-cache-private') / RUN_ID
    path = private / 'resource-phase.json'
    if not path.exists():
        if (private / 'v7-coordinator-binding.json').exists():
            raise ValueError('Source-bound PostgreSQL phase budget missing')
        return TARGET.copy(), None
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise ValueError('PostgreSQL phase policy must be root-private')
    binding = json.loads((private / 'cached-source-binding.json').read_text())
    policy = json.loads(path.read_text())
    target = phase_target(policy, binding['candidate_archive_sha256'])
    return target, {**policy, 'policy_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def apply_limits(group, target=None):
    target = target or TARGET
    original = read_limits(group)
    if memory_headroom(group) > int(target['memory.max']) - 1024**3:
        raise ValueError('Need 1GiB PostgreSQL nonreclaimable memory headroom before applying 4GiB limit')
    try:
        for name in ('memory.max', 'memory.swap.max', 'cpu.max', 'cpu.weight'):
            (Path(group) / name).write_text(target[name] + '\n')
        if read_limits(group) != target:
            raise ValueError('Target kernel resource limits were not enforced')
    except Exception:
        restore_limits(group, original)
        raise
    return original


def short_evidence_valid(rows, observation, source_sha):
    if set(rows) != {'50', '300'}:
        return False
    config = rows['50'][0].get('source_config', {})
    return (config.get('source_sha256') == source_sha and config.get('postgres_memory_gib') == 4
        and config.get('postgres_cpus') == 5 and config.get('dynamic_cpu_control') is False
        and all(len(values) == 2 and all(value.get('student_count') == int(n)
            and value.get('source_config') == config and value.get('passed') is True
            and value.get('checks') and all(check is True for check in value['checks'].values()) for value in values)
            for n, values in rows.items())
        and observation.get('oom_kills') == 0 and observation.get('memory_cpu_samples')
        and observation.get('swap_start_bytes') is not None and observation.get('swap_end_bytes') is not None
        and observation['swap_end_bytes'] <= observation['swap_start_bytes']
        and observation.get('started_at', float('inf')) <= min(values[0]['started'] for values in rows.values())
        and observation.get('ended_at', 0) >= max(values[0]['ended'] for values in rows.values()))


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).strip()


def terminate_owned_pipeline(parent, child_path):
    if child_path.exists():
        child = int(child_path.read_text())
        try:
            if child <= 1 or os.getpgid(child) != child or int(subprocess.check_output(
                ['ps', '-o', 'ppid=', '-p', str(child)], text=True).strip()) != parent:
                raise ValueError('Cannot verify test pipeline child before resource restoration')
            os.killpg(child, signal.SIGTERM)
        except ProcessLookupError:
            # A finished run_step may leave its PID while bootstrap cleanup is
            # interrupted. Still stop the already verified bootstrap parent.
            pass
    os.kill(parent, signal.SIGTERM)


def stop_test_workload():
    # Prevent the privileged bootstrap from creating another load container
    # after currently running containers have been stopped.
    parents = []
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            words = (directory / 'cmdline').read_bytes().split(b'\0')
            if (directory.stat().st_uid != 0 or not any(word.endswith(b'school_candidate_bootstrap.sh') for word in words)
                or (directory / 'cwd').resolve() != Path('/home/ktdata/school-cache-candidate')
                or ('SCHOOL_RESUME_RUN_ID=' + RUN_ID).encode() not in (directory / 'environ').read_bytes().split(b'\0')):
                continue
            parent = int(directory.name)
            child_path = Path('/home/ktdata/school-cache-private') / RUN_ID / 'active-process.pid'
            terminate_owned_pipeline(parent, child_path)
            parents.append(directory)
        except FileNotFoundError:
            continue
        except ProcessLookupError:
            continue
    ids = docker('ps', '-q', '--filter', 'label=com.docker.compose.project=kt-school-e2e').split()
    if ids:
        items = json.loads(docker('inspect', *ids))
        if any(item['Config']['Labels'].get('com.docker.compose.project') != 'kt-school-e2e'
               or item['Config']['Labels'].get('com.docker.compose.service') == 'db' for item in items):
            raise ValueError('Refuse stopping unexpected test workload')
        subprocess.run(['docker', 'stop', '--time', '15', *ids], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
    if docker('ps', '-q', '--filter', 'label=com.docker.compose.project=kt-school-e2e'):
        raise ValueError('Test workload remains active; keep PostgreSQL capped')
    deadline = time.monotonic() + 45
    while any(path.exists() for path in parents) and time.monotonic() < deadline:
        time.sleep(1)
    if any(path.exists() for path in parents):
        raise ValueError('Test bootstrap remains active; keep PostgreSQL capped')


def restore_after_failure(group, original, profile):
    stop_test_workload()
    profile['test_workload_stopped_before_restore'] = True
    mark_candidate_failed(profile)
    restore_limits(group, original)
    profile.update(state='failed-original-restored', original_limits_restored=True,
                   restored_at=time.time())


def failed_candidate_status(current, profile):
    if current.get('run_id') != RUN_ID or current.get('state') != 'running':
        return None
    return {'run_id': RUN_ID, 'state': 'failed', 'stage': current.get('stage'),
        'exit_code': 1, 'at': time.time(), 'production_cutover_performed': False,
        'failure_detected_by': 'independent-postgres-resource-helper',
        'test_workload_stopped': True, 'failure_error_type': profile.get('error_type', 'RuntimeError')}


def mark_candidate_failed(profile):
    if profile.get('run_id') != RUN_ID:
        return
    import pwd
    path = REPORT / 'status.json'
    value = failed_candidate_status(json.loads(path.read_text()), profile)
    if value is None:
        return
    temporary = path.with_suffix('.resource-failed.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)
    path.chmod(0o644)
    user = pwd.getpwnam('ktdata')
    os.chown(path, user.pw_uid, user.pw_gid)


def inspect_database():
    item = json.loads(docker('inspect', CONTAINER))[0]
    labels = item['Config']['Labels']
    if (not item['State']['Running'] or labels.get('com.docker.compose.project') != 'kt_plantform'
        or labels.get('com.docker.compose.service') != 'db' or item['Config']['Image'] != 'postgres:17-alpine'):
        raise ValueError('Expected live school production PostgreSQL container')
    image_id = docker('image', 'inspect', 'postgres:17-alpine', '--format', '{{.Id}}')
    if item['Image'] != image_id:
        raise ValueError('PostgreSQL immutable image identity mismatch')
    mounts = [mount for mount in item['Mounts'] if mount['Destination'] == '/var/lib/postgresql/data']
    if len(mounts) != 1 or mounts[0]['Type'] != 'volume' or mounts[0]['Name'] != 'kt_plantform_postgres_data':
        raise ValueError('PostgreSQL physical volume identity mismatch')
    networks = item['NetworkSettings']['Networks']
    if 'school-test-postgres' not in networks.get('kt-school-e2e_default', {}).get('Aliases', ()) or 'db' not in networks.get('kt_plantform_default', {}).get('Aliases', ()):
        raise ValueError('Shared PostgreSQL production/test aliases mismatch')
    return item


def cgroup_path(item):
    pid = item['State']['Pid']
    rows = (Path('/proc') / str(pid) / 'cgroup').read_text().splitlines()
    unified = [row.split(':', 2)[2] for row in rows if row.startswith('0::')]
    if len(unified) != 1:
        raise ValueError('This independent limiter requires verified cgroupv2')
    root = Path('/sys/fs/cgroup')
    group = (root / unified[0].lstrip('/')).resolve()
    if not group.is_relative_to(root) or item['Id'] not in str(group):
        raise ValueError('PostgreSQL cgroup path does not identify the exact container')
    return group


def pool_aliases_ready():
    ids = docker('ps', '-q', '--filter', 'label=com.docker.compose.project=kt-school-e2e',
        '--filter', 'label=com.docker.compose.service=pgbouncer').split()
    if not ids:
        return False
    if len(ids) != 1:
        raise ValueError('Test PgBouncer is ambiguous')
    rows = docker('exec', ids[0], 'sh', '-c', 'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -A -t -c "SHOW DATABASES"').splitlines()
    found = set()
    for row in rows:
        fields = row.split('|')
        if len(fields) >= 4 and fields[0] in ('kt_core', 'kt_student', 'kt_teacher', 'kt_background'):
            if fields[1:4] != ['school-test-postgres', '5432', 'kt_e2e']:
                raise ValueError('Test PgBouncer physical database alias mismatch')
            found.add(fields[0])
    return len(found) == 4


def write_report(value):
    import pwd
    path = REPORT / 'postgres-resource-profile.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)
    path.chmod(0o644)
    user = pwd.getpwnam('ktdata')
    os.chown(path, user.pw_uid, user.pw_gid)


def safe_error_reason(error):
    # Only our constant diagnostics are safe; never echo Docker/SQL/parser text.
    allowed = {
        'Need 1GiB PostgreSQL nonreclaimable memory headroom before applying 4GiB limit',
        'Limits must be applied before history or formal workload starts',
        'Expected live school production PostgreSQL container',
        'PostgreSQL immutable image identity mismatch', 'PostgreSQL physical volume identity mismatch',
        'Shared PostgreSQL production/test aliases mismatch',
        'This independent limiter requires verified cgroupv2',
        'PostgreSQL cgroup path does not identify the exact container',
        'Target kernel resource limits were not enforced',
        'Kernel resource restoration did not match original values',
        'PostgreSQL identity or resource limits changed during acceptance',
        'Candidate run identity changed', 'Test PgBouncer is ambiguous',
        'Test PgBouncer physical database alias mismatch',
        'Pool aliases were not verified before workload',
        'Candidate pipeline failed; restore prior PostgreSQL limits',
        'Formal evidence does not cover enforced PostgreSQL profile',
        'Resource observation timed out', 'Test workload remains active; keep PostgreSQL capped',
        'Test bootstrap remains active; keep PostgreSQL capped',
        'Source-bound PostgreSQL phase budget mismatch', 'Source-bound PostgreSQL phase budget missing',
        'PostgreSQL phase policy must be root-private', 'Initial short phase requires4GiB PostgreSQL memory',
    }
    return str(error) if isinstance(error, ValueError) and str(error) in allowed else type(error).__name__


def main():
    import fcntl
    if os.geteuid() != 0:
        raise SystemExit('Authenticated sudo required')
    lock = open('/run/lock/kt-school-pg-capacity-limits.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    original = group = None
    accepted = False
    profile = {'run_id': RUN_ID, 'state': 'preflight', 'target_kernel_limits': TARGET,
               'limiter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'database_restarted': False, 'database_data_modified': False,
               'docker_hostconfig_modified': False, 'enforcement': 'live-cgroupv2', 'samples': []}
    cleaned = False
    def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        if original is not None and not accepted:
            try:
                restore_after_failure(group, original, profile)
                profile['restored_kernel_limits'] = read_limits(group)
            except Exception as error:
                profile.update(state='failed-target-retained-stop-required', original_limits_restored=False,
                               cleanup_error_type=type(error).__name__)
                write_report(profile)
                raise
        write_report(profile)
    atexit.register(cleanup)
    try:
        state = json.loads((REPORT / 'status.json').read_text())
        if state.get('run_id') != RUN_ID or state.get('state') != 'running' or state.get('stage') not in ALLOWED_START:
            raise ValueError('Limits must be applied before history or formal workload starts')
        item = inspect_database()
        group = cgroup_path(item)
        target, phase_policy = run_target()
        profile.update(target_kernel_limits=target, resource_phase_policy=phase_policy)
        profile.update(container_id=item['Id'], postgres_image_id=item['Image'], postgres_volume='kt_plantform_postgres_data',
            original_docker_resources={name: item['HostConfig'][name] for name in ('Memory', 'NanoCpus', 'MemorySwap')},
            original_kernel_limits=read_limits(group), postgres_pid=item['State']['Pid'])
        profile['nonreclaimable_memory_before_bytes'] = memory_headroom(group)
        original = apply_limits(group, target)
        profile.update(state='enforcing', applied_at=time.time(), pool_aliases_verified=False)
        write_report(profile)
        deadline = time.monotonic() + 10800
        while time.monotonic() < deadline:
            current = inspect_database()
            if current['Id'] != item['Id'] or current['State']['Pid'] != item['State']['Pid'] or read_limits(group) != target:
                raise ValueError('PostgreSQL identity or resource limits changed during acceptance')
            state = json.loads((REPORT / 'status.json').read_text())
            if state.get('run_id') != RUN_ID:
                raise ValueError('Candidate run identity changed')
            if state.get('state') == 'failed':
                raise ValueError('Candidate pipeline failed; restore prior PostgreSQL limits')
            if pool_aliases_ready():
                profile['pool_aliases_verified'] = True
            if state.get('stage') in ('baseline-history-http', 'mixed-history-http', 'baseline-30-minute', 'mixed-30-minute') and not profile['pool_aliases_verified']:
                raise ValueError('Pool aliases were not verified before workload')
            profile['samples'].append({'at': time.time(), 'stage': state.get('stage'),
                'memory_current_bytes': int((group / 'memory.current').read_text()), 'limits_match': True})
            if state.get('state') == 'short-completed':
                rows = {str(n): [json.loads((REPORT / name).read_text()) for name in
                    (f'short-{n}.json', f'short-{n}-persistence.json')] for n in (50, 300)}
                observation = json.loads((REPORT / 'short-observation.json').read_text())
                if (not profile['pool_aliases_verified'] or not short_evidence_valid(rows, observation,
                    profile['resource_phase_policy']['source_sha256'])
                    or min(values[0]['started'] for values in rows.values()) < profile['applied_at']):
                    raise ValueError('Fixed short evidence does not cover enforced PostgreSQL profile')
                # Bootstrap has completed and stopped its containers before
                # publishing this distinct state. Recheck before restoring.
                time.sleep(2)
                stop_test_workload()
                restore_limits(group, original)
                accepted = True
                profile.update(state='short-completed-original-restored', ended_at=time.time(),
                    short_capture_checks_passed=True, formal_acceptance_passed=False,
                    original_limits_restored=True, test_workload_stopped_before_restore=True,
                    restored_kernel_limits=read_limits(group))
                write_report(profile)
                return
            if state.get('state') == 'passed':
                evidence = json.loads((REPORT / 'acceptance.json').read_text())
                phases = [json.loads((REPORT / name).read_text()) for name in ('baseline.json', 'mixed.json')]
                if not evidence.get('passed') or not profile['pool_aliases_verified'] or any(phase['started_at'] < profile['applied_at'] for phase in phases):
                    raise ValueError('Formal evidence does not cover enforced PostgreSQL profile')
                accepted = True
                profile.update(state='passed-target-retained', ended_at=time.time(),
                    formal_acceptance_passed=True, original_limits_restored=False)
                write_report(profile)
                return
            write_report(profile)
            time.sleep(5)
        raise ValueError('Resource observation timed out')
    except Exception as error:
        profile.update(state='failed', error_type=type(error).__name__, error_reason=safe_error_reason(error), ended_at=time.time())
        raise
    finally:
        try:
            cleanup()
        finally:
            atexit.unregister(cleanup)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise RuntimeError('Resource observation interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except Exception as error:
        print('PostgreSQL resource helper failed: ' + safe_error_reason(error))
        raise SystemExit(1)
