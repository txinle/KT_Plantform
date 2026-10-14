#!/usr/bin/env python3
"""Bounded capture-priority control for explicitly owned school test containers.

No request/data writes, process termination or PostgreSQL setting changes. A
45-second file lease lets readers recover if the controller exits unexpectedly.
CPU weight is a contention preference; neither weight nor quota reserves cores.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import signal
import subprocess
import time
from collections import deque
from pathlib import Path


NORMAL_CPUS = {'web': 6, 'student-read': 1, 'teacher-read': .5,
               'analytics-worker': .75, 'student-comparison-worker': .5,
               'export-worker': .25}
PRIORITY_CPUS = {**NORMAL_CPUS, 'web': 12, 'student-read': .5,
                 'teacher-read': .25, 'analytics-worker': .5,
                 'student-comparison-worker': .25, 'export-worker': .125}
WRITE_LIMITS = {'start': 5, 'resume': 5, 'save': 1, 'events': 1, 'submit': 2, 'finish': 2}
POLICY = {'write_limits_seconds': WRITE_LIMITS, 'priority_fraction': .8,
          'entry_samples': 2, 'clear_samples': 6, 'hold_seconds': 60,
          'recovery_seconds': 30, 'sample_seconds': 5, 'lease_seconds': 45,
          'normal_cpus': NORMAL_CPUS, 'priority_cpus': PRIORITY_CPUS}


def cpu_budgets(mode):
    return dict(PRIORITY_CPUS if mode in ('capture_priority', 'capture_emergency') else NORMAL_CPUS)


def signal_only_policy():
    return {**{key: value for key, value in POLICY.items()
               if key not in ('normal_cpus', 'priority_cpus')},
            'dynamic_cpu_control': False, 'monitored_pools': ['assessment', 'capture']}


def log_metrics(lines, pools=None):
    timings, errors = {}, 0
    for line in lines:
        try:
            row = json.loads(line)
            if pools is not None and row.get('pool') not in pools:
                continue
            operation = row['operation']
            seconds = float(row['request_time'])
            if operation not in WRITE_LIMITS or not math.isfinite(seconds) or seconds < 0:
                continue
            timings.setdefault(operation, []).append(seconds)
            errors += int(int(row.get('status', 200)) >= 500)
        except (ValueError, KeyError, TypeError):
            continue
    p95 = {name: sorted(values)[max(0, math.ceil(len(values) * .95) - 1)]
           for name, values in timings.items()}
    return {'write_p95': p95, 'write_requests': sum(map(len, timings.values())), 'write_errors': errors}


class PressureState:
    def __init__(self):
        self.mode, self.since = 'normal', 0
        self.high, self.clear = 0, 0

    def update(self, sample, now):
        latencies = sample['write_p95']
        emergency = any(value > WRITE_LIMITS[name] for name, value in latencies.items())
        emergency |= sample['memory_available_bytes'] < 2560 * 1024**2
        emergency |= sample.get('write_errors', 0) > 0
        high = emergency or any(value > .8 * WRITE_LIMITS[name] for name, value in latencies.items())
        high |= sample['pool_waiting'] > 0 and sample['core_throttle_ratio'] > .2
        self.high = self.high + 1 if high else 0
        self.clear = 0 if high else self.clear + 1
        target = self.mode
        if self.high >= 2:
            target = 'capture_emergency' if emergency else 'capture_priority'
            # Do not lower emergency while still overloaded.
            if self.mode == 'capture_emergency':
                target = self.mode
        elif self.mode in ('capture_priority', 'capture_emergency'):
            if now - self.since >= 60 and self.clear >= 6:
                target = 'recovering'
        elif self.mode == 'recovering' and now - self.since >= 30 and self.clear >= 6:
            target = 'normal'
        if target != self.mode:
            self.mode, self.since = target, now
        return self.mode


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=15).strip()


def atomic_json(path, data):
    if path.is_symlink():
        raise ValueError('Refusing symlink output')
    temporary = path.with_suffix('.tmp')
    if temporary.is_symlink():
        raise ValueError('Refusing symlink temporary output')
    temporary.write_text(json.dumps(data, separators=(',', ':')) + '\n')
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def group_for(item):
    pid = item['State']['Pid']
    if pid <= 1:
        raise ValueError('Expected running owned container')
    relative = Path(f'/proc/{pid}/cgroup').read_text().strip().split('0::', 1)[1]
    group = (Path('/sys/fs/cgroup') / relative.lstrip('/')).resolve()
    if not group.is_relative_to(Path('/sys/fs/cgroup')) or item['Id'] not in str(group):
        raise ValueError('Invalid container cgroup')
    return group


def validate_production_binding(binding, images, controller_sha):
    if (binding.get('formal_accepted') is not True or binding.get('runtime_images') != images
        or binding.get('controller_sha256') != controller_sha
        or any(not re.fullmatch('[a-f0-9]{64}', binding.get(name, ''))
               for name in ('source_sha256', 'frontend_sha256'))):
        raise ValueError('Production pressure controller requires accepted source, frontend and exact images')


def recovery_limits(actual):
    normal = {role: f'{round(cpu * 100000)} 100000' for role, cpu in NORMAL_CPUS.items()}
    priority = {role: f'{round(cpu * 100000)} 100000' for role, cpu in PRIORITY_CPUS.items()}
    if set(actual) != set(normal) or any(actual[role] not in (normal[role], priority[role]) for role in normal):
        raise ValueError('Actual CPU limits do not match capture policy')
    return normal


def pressure_directory(volume, docker_root, project):
    name = f'{project}_runtime_control'
    labels = volume.get('Labels') or {}
    if (volume.get('Name') != name or volume.get('Driver') != 'local'
            or volume.get('Scope') != 'local'
            or labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.volume') != 'runtime_control'):
        raise ValueError('Pressure volume ownership mismatch')
    root = Path(docker_root)
    if not root.is_absolute():
        raise ValueError('Docker data root must be absolute')
    directory = Path(volume['Mountpoint']).resolve()
    if directory != root.resolve() / 'volumes' / name / '_data':
        raise ValueError('Unexpected local pressure volume')
    return directory


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, choices=['kt-school-e2e', 'kt_plantform'])
    parser.add_argument('--production-binding')
    parser.add_argument('--output', required=True)
    parser.add_argument('--signal-only', action='store_true',
                        help='Six-pool profile: publish pressure without changing CPU limits.')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Fixed controller requires host root')
    lock = open(f'/run/lock/{args.project}-capture-priority.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    report = Path(args.output)
    production = args.project == 'kt_plantform'
    report_root = Path('/home/ktdata/school-promotion-reports' if production else '/home/ktdata/school-cache-reports')
    if (not report.resolve().is_relative_to(report_root) or report.name != 'pressure.json'
        or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', report.parent.name)
        or report.parent.parent != report_root):
        raise SystemExit('Invalid report destination')
    if production:
        binding_path = Path('/home/ktdata/school-promotion-private') / report.parent.name / 'pressure-binding.json'
        if args.production_binding != str(binding_path):
            raise ValueError('Explicit root-private production binding required')
    else:
        if args.production_binding:
            raise ValueError('Test controller cannot use a production binding')
        binding_path = Path('/home/ktdata/school-cache-private') / report.parent.name / 'cached-source-binding.json'
    if binding_path.is_symlink() or binding_path.stat().st_uid != 0 or binding_path.stat().st_mode & 0o077:
        raise ValueError('Controller binding must remain root-private')
    binding = json.loads(binding_path.read_text())
    if args.signal_only and binding.get('pool_profile') != 'six-pools-180-v1':
        raise ValueError('Signal-only monitoring requires the frozen six-pool profile')
    policy = signal_only_policy() if args.signal_only else POLICY
    evidence_binding = {'source_sha256': binding['source_sha256'] if production else binding['candidate_archive_sha256'],
                        'frontend_sha256': binding['frontend_sha256'] if production else binding['frontend_archive_sha256'],
                        'controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    ids = command('docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={args.project}').split()
    items = json.loads(command('docker', 'inspect', *ids))
    by_role = {}
    for item in items:
        labels = item['Config']['Labels']
        if labels.get('com.docker.compose.project') != args.project:
            raise ValueError('Project ownership mismatch')
        if production and labels.get('com.docker.compose.project.working_dir') != '/data/kt-platform/KT_Plantform':
            raise ValueError('Production controller working directory mismatch')
        role = labels['com.docker.compose.service']
        by_role.setdefault(role, []).append(item)
    controlled_roles = (('auth', 'assessment', 'capture', 'web', 'student-read', 'teacher-read',
                         'analytics-worker', 'student-comparison-worker', 'publication-worker',
                         'export-worker') if args.signal_only else tuple(NORMAL_CPUS))
    if any(len(by_role.get(role, [])) != 1 for role in (*controlled_roles, 'nginx', 'pgbouncer')):
        raise ValueError('Requires exactly one running process container for each controlled role')
    if production:
        images = {role: by_role[role][0]['Image'] for role in (*controlled_roles, 'nginx', 'pgbouncer')}
        validate_production_binding(binding, images, evidence_binding['controller_sha256'])
    if args.signal_only:
        images = {role: by_role[role][0]['Image'] for role in (*controlled_roles, 'nginx', 'pgbouncer')}
        if any(binding.get('runtime_images', {}).get(role) != image for role, image in images.items()):
            raise ValueError('Signal-only monitored images differ from the frozen binding')
    groups = {role: group_for(by_role[role][0]) for role in controlled_roles}
    actual = {role: (group / 'cpu.max').read_text().strip() for role, group in groups.items()}
    original_cpu_max = {} if args.signal_only else recovery_limits(actual)
    # Recover a killed controller's known temporary quotas before restarting.
    for role, value in original_cpu_max.items():
        (groups[role] / 'cpu.max').write_text(value + '\n')
    volume = json.loads(command('docker', 'volume', 'inspect', f'{args.project}_runtime_control'))[0]
    docker_root = command('docker', 'info', '--format', '{{.DockerRootDir}}').strip()
    directory = pressure_directory(volume, docker_root, args.project)
    path = directory / 'pressure.json'
    controller = PressureState()
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    started, cursor = time.time(), time.time()
    samples, transitions = deque(maxlen=720), deque(maxlen=1440)
    sample_count = 0
    last_stat, active = None, 'normal'
    failure = None
    try:
        while not stopped:
            now = time.time()
            raw = command('docker', 'logs', '--since', str(cursor), '--until', str(now),
                          '--tail', '10000', by_role['nginx'][0]['Id'])
            cursor = now
            sample = log_metrics(raw.splitlines(), pools=('assessment', 'capture') if args.signal_only else None)
            cpu_roles = ('assessment', 'capture') if args.signal_only else ('web',)
            cpu = {role: dict((name, int(value)) for name, value in
                    (line.split() for line in (groups[role] / 'cpu.stat').read_text().splitlines()))
                   for role in cpu_roles}
            previous_cpu = last_stat or cpu
            sample['core_throttle_ratio'] = max(
                max(0, (cpu[role].get('nr_throttled', 0) - previous_cpu[role].get('nr_throttled', 0)) /
                    (cpu[role].get('nr_periods', 0) - previous_cpu[role].get('nr_periods', 0)))
                if cpu[role].get('nr_periods', 0) > previous_cpu[role].get('nr_periods', 0) else 0
                for role in cpu_roles)
            last_stat = cpu
            sample['memory_available_bytes'] = next(int(line.split()[1]) * 1024
                for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
            pools = command('docker', 'exec', by_role['pgbouncer'][0]['Id'], 'sh', '-c',
                'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -h 127.0.0.1 -U "$POSTGRES_USER" -d pgbouncer --csv -c "SHOW POOLS"')
            sample['pool_waiting'] = sum(int(row['cl_waiting']) for row in csv.DictReader(io.StringIO(pools))
                                         if row['database'] in (
                                             ('kt_assessment', 'kt_capture') if args.signal_only else ('kt_core',)))
            sample['at'] = now
            mode = controller.update(sample, now)
            if mode != active:
                if not args.signal_only:
                    for role, cpus in cpu_budgets(mode).items():
                        (groups[role] / 'cpu.max').write_text(f'{round(cpus * 100000)} 100000\n')
                transitions.append({'at': now, 'before': active, 'after': mode, 'reason_sample': sample})
                active = mode
            retry = {'normal': 5, 'capture_priority': 15, 'capture_emergency': 30, 'recovering': 10}[mode]
            atomic_json(path, {'mode': mode, 'expires_at': now + 45, 'retry_after_seconds': retry})
            samples.append(sample)
            sample_count += 1
            atomic_json(report, {**evidence_binding, 'state': 'running', 'mode': mode, 'project': args.project, 'started_at': started,
                'policy': policy, 'policy_sha256': hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
                'transitions': list(transitions), 'samples': list(samples), 'sample_count': sample_count,
                'samples_window_max': samples.maxlen, 'actual_cpu_max':
                {role: (group / 'cpu.max').read_text().strip() for role, group in groups.items()}})
            time.sleep(5)
    except Exception as error:
        failure = type(error).__name__
        raise
    finally:
        restored = {}
        for role, value in original_cpu_max.items():
            try:
                (groups[role] / 'cpu.max').write_text(value + '\n')
                restored[role] = (groups[role] / 'cpu.max').read_text().strip() == value
            except OSError:
                restored[role] = False
        atomic_json(path, {'mode': 'normal', 'expires_at': time.time(), 'retry_after_seconds': 5})
        atomic_json(report, {**evidence_binding, 'state': 'failed' if failure or not all(restored.values()) else 'stopped-restored',
            'project': args.project, 'mode': 'normal', 'started_at': started, 'ended_at': time.time(),
            'failure_type': failure, 'restored': restored, 'last_active_mode': active, 'policy': policy,
            'full_restoredbool': args.signal_only or bool(restored) and all(restored.values()),
            'transitions': list(transitions), 'samples': list(samples), 'sample_count': sample_count,
            'samples_window_max': samples.maxlen})


if __name__ == '__main__':
    main()
