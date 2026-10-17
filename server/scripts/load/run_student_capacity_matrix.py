"""Bounded capacity matrix for the explicitly owned 20261016 isolated project.

All HTTP drivers run in separate same-DUT containers. Protocol results do not
establish browser operability, campus network performance or production capacity.
"""
import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
from collect_student_capacity_logs import ROLES as LOG_ROLES
from prepare_student_capacity_remote import ROOT, PROJECT, PORT
import student_capacity_protocol as protocol
import student_concurrency_protocol as legacy_protocol

WRITE_CANDIDATES = ('B', 'W8D4', 'W8D8', 'W12D8', 'W12D12')
ALL_MODES = ('login', 'startup', 'login-mixed', 'startup-mixed', 'full-flow',
             'submission', 'save-only', 'submission-mixed')
ACTIVE = {'db', 'pgbouncer', 'migrate', 'web', 'entry', 'assessment', 'capture',
          'teacher-read', 'scheduler', 'analytics-worker', 'session-submission-worker', 'nginx'}


def cells(stage, candidates, modes, *, counts=(50, 180), rounds=1):
    return [{'stage': stage, 'candidate': candidate, 'count': count, 'mode': mode, 'round': repeat}
        for candidate in candidates for repeat in range(1, rounds + 1) for count in counts for mode in modes]


def protocol_for_cell(cell):
    selected = cell.get('transport_protocol', 'capacity-v2')
    if selected == 'legacy-v2':
        if (cell.get('stage') != 'legacy-control' or cell.get('candidate') != 'B'
                or cell.get('count') not in (50, 180)
                or cell.get('mode') not in ('login-mixed', 'startup-mixed')):
            raise ValueError('Legacy control is bounded to B50/180 login/startup mixed and excluded from qualification')
        return legacy_protocol, 'student_concurrency_protocol.py'
    if selected != 'capacity-v2':
        raise ValueError('Unknown transport protocol')
    return protocol, 'student_capacity_protocol.py'


def select_candidate(results, candidates, modes, *, metric='score_ms'):
    results = [row for row in results if row.get('transport_protocol') != 'legacy-v2'
               and row.get('stage') != 'legacy-control']
    required = {(count, mode) for count in (50, 180) for mode in modes}
    for candidate in candidates:
        rows = [row for row in results if row['candidate'] == candidate]
        if {(row['count'], row['mode']) for row in rows if row['passed']} >= required:
            return {'candidate': candidate, 'qualified': True, 'reason': 'smallest_fully_passing_candidate'}
    def score(candidate):
        rows = [row.get(metric) for row in results if row['candidate'] == candidate and row['count'] == 180]
        valid = [value for value in rows if isinstance(value, (float, int)) and math.isfinite(value)]
        return max(valid) if valid else float('inf')
    selected = min(candidates, key=score)
    return {'candidate': selected, 'qualified': False, 'reason': 'fastest_observed_180_limited_candidate'
        if math.isfinite(score(selected)) else 'no_comparable_timing_smallest_candidate_retained'}


def cell_verdict(summary, verification, *, monitor_ok):
    counts = summary.get('counts', {})
    denominator = (isinstance(counts.get('planned'), int) and counts['planned'] > 0
        and counts.get('completed') == counts['planned']
        and all(counts.get(key) == 0 for key in ('missing', 'failed', 'invalid')))
    safety = not monitor_ok or verification.get('safety_stop') is not False
    return {'passed': bool(denominator and summary.get('thresholds_met') is True
        and verification.get('integrity_passed') is True and verification.get('workflow_complete') is True
        and verification.get('stats_ready') is True and not safety),
        'safety_stop': safety, 'full_denominator_completed': denominator,
        'performance_passed': summary.get('thresholds_met') is True,
        'integrity_passed': verification.get('integrity_passed') is True,
        'stats_ready': verification.get('stats_ready') is True}


def all_modes_passed(rows):
    return {row['mode'] for row in rows if row['count'] == 180 and row.get('passed')
        and row.get('transport_protocol') != 'legacy-v2' and row.get('stage') != 'legacy-control'} == set(ALL_MODES)


@dataclass
class SafetyWindow:
    last_memory_at: float | None = None
    low_samples: int = 0

    def sample(self, now, available, *, restarted=False, oom=False):
        if restarted:
            return 'unexpected_container_restart'
        if oom:
            return 'owned_container_oom'
        if available is None:
            return 'memory_monitor_missing'
        if self.last_memory_at is None or now - self.last_memory_at >= 15:
            self.last_memory_at = now
            self.low_samples = self.low_samples + 1 if available < 2 * 1024**3 else 0
        return 'memory_below_2GiB_three_15s_samples' if self.low_samples >= 3 else None


def validate_compose(config, path):
    if config.get('name') != PROJECT or not Path(path).resolve().is_relative_to((ROOT / 'private').resolve()):
        raise ValueError('Only the explicitly owned capacity project and private compose files are permitted')
    for kind in ('networks', 'volumes'):
        for item in config.get(kind, {}).values():
            if item.get('external') or not item.get('name', '').startswith(PROJECT + '_'):
                raise ValueError('External or other-project network/volume rejected')
    for role, service in config['services'].items():
        if service.get('profiles'):
            continue
        if role not in ACTIVE or service.get('container_name'):
            raise ValueError('Unknown service or hardcoded container identity rejected')
        env = service.get('environment', {})
        if role not in ('nginx',) and env.get('POSTGRES_DB') != 'kt_e2e':
            raise ValueError('Only isolated kt_e2e database permitted')
        for port in service.get('ports', []):
            if role != 'nginx' or (port.get('host_ip'), str(port.get('published')), port.get('target')) != ('127.0.0.1', str(PORT), 80):
                raise ValueError('Only the designated loopback capacity port is permitted')
        for mount in service.get('volumes', []):
            if mount['type'] == 'bind' and not Path(mount['source']).resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('Bind mounts must remain inside the owned capacity root')
            if mount['type'] == 'volume' and mount['source'] not in config.get('volumes', {}):
                raise ValueError('Unknown volume rejected')


def parameters(config):
    env = config['services']['entry']['environment']
    values = {key: str(value) for key, value in env.items()
              if (key.startswith(('ASSESSMENT_', 'SCHOOL_DB_', 'PGBOUNCER_'))
                  or key in ('SCHOOL_POOL_PROFILE', 'DATABASE_CONN_MAX_AGE', 'SCHOOL_REDIS_SESSION_ENABLED'))
              and not any(token in key for token in ('PASSWORD', 'SECRET', 'TOKEN', 'ENCRYPTION_KEY'))}
    for role, service in config['services'].items():
        for key in ('cpus', 'cpuset', 'cpu_shares', 'mem_limit', 'memswap_limit', 'cgroup_parent'):
            if key in service:
                values[f'resource.{role}.{key}'] = str(service[key])
    return values


def modified(config, changes):
    result = copy.deepcopy(config)
    resources = {key: value for key, value in changes.items() if key.startswith('resource.')}
    for key, value in resources.items():
        _, role, field = key.split('.', 2)
        if role not in result['services'] or field not in ('mem_limit', 'memswap_limit', 'cpus', 'cpuset', 'cpu_shares'):
            raise ValueError('Unknown retest service resource setting')
        result['services'][role][field] = value
        if field in ('mem_limit', 'cpus'):
            limit_key = 'memory' if field == 'mem_limit' else 'cpus'
            deployment = result['services'][role].get('deploy', {})
            if limit_key in deployment.get('resources', {}).get('limits', {}):
                deployment['resources']['limits'][limit_key] = value
    changes = {key: str(value) for key, value in changes.items() if not key.startswith('resource.')}
    for role, service in result['services'].items():
        if service.get('profiles'):
            continue
        if role == 'db':
            if 'SCHOOL_DB_MAX_CONNECTIONS' in changes:
                command = service.get('command', [])
                if not isinstance(command, list):
                    raise ValueError('Explicit PostgreSQL argv required for connection-budget changes')
                found = any(str(value).startswith('max_connections=') for value in command)
                service['command'] = [('max_connections=' + changes['SCHOOL_DB_MAX_CONNECTIONS'])
                    if str(value).startswith('max_connections=') else value for value in command]
                if not found:
                    service['command'] += ['-c', 'max_connections=' + changes['SCHOOL_DB_MAX_CONNECTIONS']]
            continue
        env = service.setdefault('environment', {})
        if role == 'nginx':
            env.update({key: value for key, value in changes.items() if key.startswith('ASSESSMENT_ENTRY_')})
            continue
        env.update({key: value for key, value in changes.items() if key != 'DATABASE_CONN_MAX_AGE'})
        if role in ('web', 'entry', 'assessment', 'capture', 'teacher-read') and 'DATABASE_CONN_MAX_AGE' in changes:
            env['DATABASE_CONN_MAX_AGE'] = changes['DATABASE_CONN_MAX_AGE']
        for service_name, key in (('entry', 'ASSESSMENT_ENTRY_WORKERS'), ('assessment', 'ASSESSMENT_REMAINING_WORKERS'),
                                 ('capture', 'ASSESSMENT_CAPTURE_WORKERS'), ('web', 'ASSESSMENT_CORE_WORKERS'),
                                 ('teacher-read', 'ASSESSMENT_TEACHER_WORKERS')):
            if role == service_name and key in changes:
                env['GUNICORN_WORKERS'] = changes[key]
        for service_name, key in (('session-submission-worker', 'ASSESSMENT_SUBMISSION_WORKERS'), ('analytics-worker', 'ASSESSMENT_ANALYTICS_WORKERS')):
            if role == service_name and key in changes:
                old_replicas = int(service.get('deploy', {}).get('replicas', 1))
                if service.get('cpu_shares'):
                    service['cpu_shares'] = max(2, round(int(service['cpu_shares']) * old_replicas / int(changes[key])))
                service.setdefault('deploy', {})['replicas'] = int(changes[key])
    return result


def write_once(path, value):
    path = Path(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


class SafetyStop(RuntimeError):
    pass


class Matrix:
    def __init__(self, run_id, diagnostic_manifest):
        self.run_id = run_id
        self.directory = ROOT / 'evidence' / ('matrix-' + run_id)
        self.directory.mkdir(mode=0o700)
        (self.directory / 'cells').mkdir(mode=0o700)
        self.configs = {}
        self.current = None
        self.results = []
        self.selections = []
        self.diagnostic_manifest = Path(diagnostic_manifest).resolve()
        if not self.diagnostic_manifest.is_relative_to((ROOT / 'evidence').resolve()):
            raise ValueError('Diagnostic manifest must stay inside owned evidence root')
        self.report = {'project': PROJECT, 'port': PORT, 'run_id': run_id, 'state': 'running',
            'same_DUT_generator': True, 'browser_operability_measured': False,
            'formal_capacity_accepted': False, 'cells': self.results, 'selections': self.selections,
            'diagnostic_manifest': str(self.diagnostic_manifest),
            'workload_bound_seconds': 600, 'statistics_observation_bound_seconds': 300,
            'scope': 'Isolated protocol matrix with same-DUT generator interference; real pages and campus network require separate evidence.'}

    def call(self, command, *, timeout=60, allowed=(0,)):
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        if result.returncode not in allowed:
            # Keep diagnostics private; command arguments/environments can contain credentials.
            with (self.directory / 'command-failures.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'at': time.time(), 'program': command[:2],
                    'exit_code': result.returncode, 'stderr': result.stderr}) + '\n')
            (self.directory / 'command-failures.jsonl').chmod(0o600)
            raise SafetyStop(f'bounded_command_failed_exit_{result.returncode}')
        return result.stdout

    def save_report(self):
        temporary = self.directory / 'matrix-report.tmp'
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.chmod(0o600)
        os.replace(temporary, self.directory / 'matrix-report.json')

    def register(self, name, config):
        if name in self.configs:
            return
        path = ROOT / 'private' / f'compose-matrix-{self.run_id}-{name}.json'
        validate_compose(config, path)
        write_once(path, config)
        self.call(['docker', 'compose', '-p', PROJECT, '-f', str(path), 'config', '--quiet'])
        self.configs[name] = (path, config)

    def compose(self, name, args, **kwargs):
        path, config = self.configs[name]
        validate_compose(config, path)
        return self.call(['docker', 'compose', '-p', PROJECT, '-f', str(path), *args], **kwargs)

    def inspect_runtime(self):
        ids = self.call(['docker', 'ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT], timeout=5).split()
        items = json.loads(self.call(['docker', 'inspect', *ids], timeout=5)) if ids else []
        if len(items) != len(ids) or {item['Id'] for item in items} != set(ids):
            raise SafetyStop('runtime_inspection_incomplete')
        return items

    def check_diagnostics(self, items=None):
        try:
            manifest = json.loads(self.diagnostic_manifest.read_text(encoding='utf-8'))
            monotonic_clock = manifest.get('freshness_clock') == 'monotonic'
            age = (time.monotonic() - manifest['updated_at_monotonic'] if monotonic_clock
                else time.time() - manifest['updated_at'])
            if (manifest['project'] != PROJECT or manifest['state'] != 'collecting'
                    or not 0 <= age <= 30 or manifest.get('coverage_gaps') != []):
                self.diagnostic_check_failure = {
                    'checked_at': time.time(), 'updated_at': manifest['updated_at'],
                    'age_seconds': age, **{key: manifest.get(key) for key in
                        ('project', 'state', 'coverage_gaps')}}
                if monotonic_clock:
                    self.diagnostic_check_failure.update(freshness_clock='monotonic',
                        updated_at_monotonic=manifest['updated_at_monotonic'])
                raise SafetyStop('diagnostic_log_coverage_failed_or_stale')
            active = {(row['container_id'], row['started_at']) for row in manifest['streams']
                if row['state'] == 'following' and row.get('history') in ('since_container_start', 'since_new_started_at')}
            if not active:
                raise SafetyStop('diagnostic_log_coverage_missing')
            if items is not None:
                required = {(item['Id'], item['State']['StartedAt']) for item in items
                    if item['Config'].get('Labels', {}).get('com.docker.compose.service') in LOG_ROLES
                    and item['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() != 'true'
                    and item['State']['Running']}
                if not required or not required <= active:
                    raise SafetyStop('diagnostic_log_lifetime_missing')
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SafetyStop('diagnostic_log_manifest_unavailable') from error

    def preserve_interrupted_cell(self, directory, cell, driver, seed_id, error):
        report = {**cell, 'run_id': seed_id, 'state': 'interrupted', 'passed': False,
            'safety_stop': True, 'integrity_passed': False, 'monitor_ok': False,
            'counts': {'planned': cell['count'], 'completed': None, 'failed': None,
                'missing': None, 'invalid': None}, 'error_type': type(error).__name__,
            'failure_reason': str(error) if isinstance(error, SafetyStop) else 'Controller exception',
            'unparseable_record_lines': 0, 'unknown_operations_preserved': True}
        try:
            plan = json.loads((directory / 'plan.json').read_text(encoding='utf-8'))
            if (plan['count'] != cell['count'] or plan['mode'] != cell['mode']
                    or plan['run_id'] != seed_id or plan['version'] != driver.VERSION):
                raise ValueError('Interrupted plan binding mismatch')
            records = []
            path = directory / 'records.jsonl'
            for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError('Invalid record')
                    records.append(record)
                except ValueError:
                    report['unparseable_record_lines'] += 1
            report['summary'] = driver.summarize(plan, records)
            report['counts'] = report['summary']['counts']
        except (OSError, ValueError, KeyError, TypeError) as evidence_error:
            report['summary_unavailable_error_type'] = type(evidence_error).__name__
        write_once(directory / 'interrupted-cell.json', report)

    def wait_for_diagnostic_coverage(self, items):
        # Compose readiness precedes the collector's next discovery. Wait before
        # any fixture/load work; runtime checks never allow this grace period.
        deadline = time.monotonic() + 10
        while True:
            try:
                self.check_diagnostics(items)
                return
            except SafetyStop as error:
                manifest = getattr(self, 'diagnostic_manifest', None)
                first_manifest_pending = (str(error) == 'diagnostic_log_manifest_unavailable'
                    and isinstance(manifest, Path) and not manifest.exists())
                if not first_manifest_pending and str(error) not in ('diagnostic_log_lifetime_missing', 'diagnostic_log_coverage_missing'):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SafetyStop('diagnostic_log_coverage_not_ready') from error
                time.sleep(min(0.25, remaining))

    @staticmethod
    def signature(items):
        return {item['Id']: item.get('RestartCount', 0) for item in items
            if item['Config'].get('Labels', {}).get('com.docker.compose.service') not in ('load', 'migrate')
            and item['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() != 'true'}

    def install(self, name):
        if self.current == name:
            return
        if self.inspect_runtime():
            # Stop first: full duplicate PgBouncer pools exceed rolling headroom.
            self.compose(self.current or name, ['stop', '--timeout', '30', *sorted(ACTIVE - {'db', 'migrate'})], timeout=120)
        self.compose(name, ['up', '-d', '--wait', '--wait-timeout', '180', *sorted(ACTIVE)], timeout=240)
        self.current = name

    def web(self):
        rows = [item for item in self.inspect_runtime() if item['Config'].get('Labels', {}).get('com.docker.compose.service') == 'web']
        if len(rows) != 1:
            raise SafetyStop('one_owned_web_required')
        return rows[0]

    def manage(self, web, arguments, *, timeout=300, allowed=(0,)):
        return self.call(['docker', 'exec', '-e', 'POSTGRES_HOST=db', '-e', 'POSTGRES_POOL_ALIAS=',
            '-e', 'DATABASE_CONN_MAX_AGE=0', web, 'python', 'manage.py', *arguments], timeout=timeout, allowed=allowed)

    def check_monitor(self, process, output, guard, baseline, last_inspect):
        if process.poll() is not None:
            raise SafetyStop('runtime_observer_exited_early')
        progress = output.with_suffix('.progress.json')
        if not progress.exists():
            raise SafetyStop('runtime_monitor_missing_or_stale')
        try:
            record = json.loads(progress.read_text())
            latest = record['latest_sample']
            monotonic_clock = record.get('freshness_clock') == 'monotonic'
            heartbeat_age = (time.monotonic() - record['heartbeat_monotonic'] if monotonic_clock
                else time.time() - progress.stat().st_mtime)
            if heartbeat_age > 30 or (monotonic_clock and heartbeat_age < 0):
                raise SafetyStop('runtime_monitor_missing_or_stale')
            if record.get('current_sample', {}).get('state') == 'failed':
                raise SafetyStop('runtime_monitor_measurement_failed')
            if not latest:
                raise SafetyStop('runtime_monitor_missing_or_stale')
            age = (time.monotonic() - latest['sample_started_monotonic'] if monotonic_clock
                else time.time() - latest['sample_started_at'])
            if not 0 <= age <= 30 or latest['sample_duration_seconds'] > 30:
                raise SafetyStop('runtime_monitor_missing_or_stale')
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SafetyStop('runtime_monitor_invalid') from error
        if latest['state'] != 'sampled':
            raise SafetyStop('runtime_monitor_measurement_failed')
        self.check_diagnostics()
        now = time.monotonic()
        reason = guard.sample(now, latest.get('memory_available_bytes'))
        if reason:
            raise SafetyStop(reason)
        if now - last_inspect >= 15:
            items = self.inspect_runtime()
            self.check_diagnostics(items)
            restarted = self.signature(items) != baseline
            oom = any(item['State'].get('OOMKilled') for item in items)
            reason = guard.sample(now, latest.get('memory_available_bytes'), restarted=restarted, oom=oom)
            if reason:
                raise SafetyStop(reason)
            age = (time.monotonic() - latest['sample_started_monotonic'] if monotonic_clock
                else time.time() - latest['sample_started_at'])
            if not 0 <= age <= 30:
                raise SafetyStop('runtime_monitor_missing_or_stale')
            last_inspect = now
        return last_inspect

    def execute(self, cell):
        driver, driver_script = protocol_for_cell(cell)
        legacy = driver is legacy_protocol
        self.check_diagnostics()
        self.install(cell['candidate'])
        self.wait_for_diagnostic_coverage(self.inspect_runtime())
        identity = hashlib.sha256(json.dumps([self.run_id, cell], sort_keys=True).encode()).hexdigest()[:20]
        seed_id = 'm' + identity
        directory = self.directory / 'cells' / seed_id
        directory.mkdir(mode=0o700)
        web = self.web()
        web_id = web['Id']
        internal = '/tmp/capacity-' + seed_id
        self.call(['docker', 'exec', web_id, 'mkdir', '-p', internal])
        self.manage(web_id, ['seed_prepared_startup_load', '--run-id', seed_id,
            '--scenario', 'mixed' if 'mixed' in cell['mode'] else 'same-task',
            '--students', str(max(180, cell['count'])), '--questions', '20', '--output', internal + '/fixture.json'])
        self.call(['docker', 'cp', web_id + ':' + internal + '/fixture.json', str(directory / 'fixture.json')])
        (directory / 'fixture.json').chmod(0o600)
        path, config = self.configs[cell['candidate']]
        write_once(directory / 'source-config.json', {'pool_profile': 'shared-entry-v2',
            'postgres_profile': 'isolated-pg17-v1', 'compose_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'transport_protocol': 'legacy-v2' if legacy else 'capacity-v2', 'protocol_version': driver.VERSION,
            'parameters': parameters(config)})
        observation = directory / 'observation.json'
        stop_file = directory / 'observer.stop'
        observer_log = (directory / 'observer.log').open('x')
        observer = subprocess.Popen([sys.executable, str(ROOT / 'source/server/scripts/load/observe_school_runtime.py'),
            '--project', PROJECT, '--duration', '940', '--interval', '1', '--scope', 'formal',
            '--abort-memory-below-mb', '0', '--stop-file', str(stop_file), '--output', str(observation)],
            stdout=observer_log, stderr=subprocess.STDOUT)
        load_name = PROJECT + '-load-' + identity
        load_started = False
        stats_started = None
        try:
            deadline = time.monotonic() + 35
            while not observation.with_suffix('.progress.json').exists():
                if observer.poll() is not None or time.monotonic() > deadline:
                    raise SafetyStop('observer_initial_sample_unavailable')
                time.sleep(0.25)
            baseline = self.signature(self.inspect_runtime())
            guard = SafetyWindow()
            inspected_at = self.check_monitor(observer, observation, guard, baseline, 0)
            command = ['docker', 'run', '--detach', '--name', load_name, '--network', 'host',
                '--label', 'com.docker.compose.project=' + PROJECT, '--label', 'com.docker.compose.service=load',
                '--memory', '1g', '--memory-swap', '1g', '--cgroup-parent', 'ktconcurrency.slice',
                '--mount', f'type=bind,src={directory},dst=/capacity', '--entrypoint', 'python', web['Image'],
                '/app/scripts/load/' + driver_script, '--fixture', '/capacity/fixture.json',
                '--mode', cell['mode'], '--count', str(cell['count']), '--run-id', seed_id,
                '--base-url', f'http://127.0.0.1:{PORT}', '--plan-output', '/capacity/plan.json',
                '--output', '/capacity/records.jsonl', '--summary', '/capacity/summary.json']
            self.call(command)
            load_started = True
            print(json.dumps({'state': 'load_started', **cell, 'container': load_name,
                'evidence': str(directory.relative_to(ROOT))}), flush=True)
            began = time.monotonic()
            timed_out = False
            while True:
                inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                status = json.loads(self.call(['docker', 'inspect', load_name], timeout=5))[0]['State']
                if not status['Running']:
                    if status.get('OOMKilled'):
                        raise SafetyStop('load_generator_oom')
                    if status['ExitCode'] not in (0, 1):
                        raise SafetyStop('load_generator_execution_failure')
                    break
                if time.monotonic() - began >= 600:
                    timed_out = True
                    self.call(['docker', 'stop', '--time', '5', load_name])
                    break
                time.sleep(1)
            (directory / 'driver.log').write_text(self.call(['docker', 'logs', load_name]))
            plan = json.loads((directory / 'plan.json').read_text())
            records = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines() if line.strip()]
            summary = driver.summarize(plan, records)
            if (plan['count'] != cell['count'] or plan['mode'] != cell['mode'] or plan['run_id'] != seed_id
                    or plan['version'] != driver.VERSION or summary['version'] != driver.VERSION):
                raise SafetyStop('frozen_plan_cell_binding_changed')
            write_once(directory / 'recomputed-summary.json', summary)
            for filename in ('plan.json', 'records.jsonl'):
                self.call(['docker', 'cp', str(directory / filename), web_id + ':' + internal + '/' + filename])
            stats_started = time.monotonic()
            attempt = 0
            verification = {'integrity_passed': False, 'workflow_complete': False,
                'safety_stop': False, 'stats_ready': False}
            while True:
                remaining = 300 - (time.monotonic() - stats_started)
                if remaining <= 0:
                    break
                inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                filename = f'verification-{attempt:03d}.json'
                self.manage(web_id, ['verify_student_capacity_load', '--fixture', internal + '/fixture.json',
                    '--plan', internal + '/plan.json', '--records', internal + '/records.jsonl', '--output', internal + '/' + filename],
                    timeout=min(30, remaining), allowed=(0, 1))
                self.call(['docker', 'cp', web_id + ':' + internal + '/' + filename, str(directory / filename)])
                verification = json.loads((directory / filename).read_text())
                if verification.get('safety_stop') is not False:
                    raise SafetyStop('independent_database_integrity_failure')
                if verification.get('stats_ready') is True or time.monotonic() - stats_started >= 300:
                    break
                for _ in range(5):
                    remaining = 300 - (time.monotonic() - stats_started)
                    if remaining <= 0:
                        break
                    inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                    time.sleep(min(1, remaining))
                attempt += 1
            verdict = cell_verdict(summary, verification, monitor_ok=True)
            if timed_out:
                verdict['passed'] = False
            metric = 'processing' if cell['stage'] == 'submission' else ('save_scheduled'
                if cell['stage'] == 'write' else 'startup')
            score = summary.get(metric, {}).get('p95_ms')
            if score is None:
                values = [summary.get(name, {}).get('p95_ms') for name in ('login', 'startup', 'save_scheduled', 'submission')]
                score = max((value for value in values if value is not None), default=None)
            return {**cell, **verdict, 'run_id': seed_id, 'score_ms': score,
                'transport': 'protocol', 'transport_protocol': 'legacy-v2' if legacy else 'capacity-v2',
                'protocol_version': driver.VERSION, 'eligible_for_qualification': not legacy,
                'timing_scope': 'Legacy receipt timings; click-to-closure submission SLA and background processing timings are not measured.'
                    if legacy else 'Browser-path-aligned protocol timings; actual browser operability is not measured.',
                'workload_timeout': timed_out, 'counts': summary['counts'],
                'timings': {key: summary.get(key) for key in ('login', 'startup', 'save', 'save_scheduled', 'submission', 'processing', 'completion')},
                'stats': verification.get('stats'), 'statistics_observation_seconds': time.monotonic() - stats_started,
                'evidence': str(directory.relative_to(ROOT)), 'parameters': parameters(config)}
        finally:
            failure = sys.exc_info()[1]
            try:
                if load_started:
                    status = json.loads(self.call(['docker', 'inspect', load_name], timeout=5))[0]
                    if status['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
                        raise SafetyStop('load_container_ownership_changed')
                    if status['State']['Running']:
                        self.call(['docker', 'stop', '--time', '5', load_name], timeout=10)
            except BaseException as cleanup_error:
                failure = failure or cleanup_error
                raise
            finally:
                stop_file.touch()
                try:
                    try:
                        observer.wait(timeout=35)
                    except subprocess.TimeoutExpired:
                        observer.terminate()
                        observer.wait(timeout=5)
                    if failure is None:
                        if (not observation.exists() or observer.returncode != 0
                                or json.loads(observation.read_text()).get('state') != 'completed'):
                            raise SafetyStop('runtime_observation_incomplete')
                        self.check_diagnostics(self.inspect_runtime())
                except BaseException as observer_error:
                    failure = failure or observer_error
                    raise
                finally:
                    observer_log.close()
                    if failure is not None:
                        self.preserve_interrupted_cell(directory, cell, driver, seed_id, failure)

    def stage(self, stage, names, modes, *, counts=(50, 180), rounds=1, transport_protocol='capacity-v2'):
        rows = []
        checked = set()
        for cell in cells(stage, names, modes, counts=counts, rounds=rounds):
            if transport_protocol != 'capacity-v2':
                cell['transport_protocol'] = transport_protocol
            protocol_for_cell(cell)
            if transport_protocol != 'legacy-v2' and counts == (50, 180) and cell['candidate'] not in checked:
                self.stage(stage + '-functional', (cell['candidate'],), modes, counts=(3,))
                checked.add(cell['candidate'])
            self.report['active_cell'] = {**cell, 'started_at': time.time()}
            self.save_report()
            row = self.execute(cell)
            self.results.append(row)
            rows.append(row)
            self.report['active_cell'] = None
            self.save_report()
            print(json.dumps({key: row[key] for key in ('stage', 'candidate', 'count', 'mode', 'round', 'passed', 'safety_stop')}), flush=True)
            if row['safety_stop']:
                raise SafetyStop('cell_integrity_or_monitor_failure')
        return rows

    def run(self):
        try:
            for name in WRITE_CANDIDATES:
                path = ROOT / 'private' / f'compose-{name}.json'
                config = json.loads(path.read_text())
                validate_compose(config, path)
                self.register(name, config)
            self.stage('legacy-control', ('B',), ('login-mixed', 'startup-mixed'), transport_protocol='legacy-v2')
            rows = self.stage('write', WRITE_CANDIDATES, ('login-mixed', 'startup-mixed'))
            selection = select_candidate(rows, WRITE_CANDIDATES, ('login-mixed', 'startup-mixed'))
            self.selections.append({'stage': 'write', **selection})
            write_config = self.configs[selection['candidate']][1]
            for count in (1, 2, 4):
                self.register('Q' + str(count), modified(write_config, {'ASSESSMENT_SUBMISSION_WORKERS': count,
                    'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': count}))
            rows = self.stage('submission', ('Q1', 'Q2', 'Q4'), ('submission', 'submission-mixed'))
            selection = select_candidate(rows, ('Q1', 'Q2', 'Q4'), ('submission', 'submission-mixed'))
            self.selections.append({'stage': 'submission', **selection})
            submission_config = self.configs[selection['candidate']][1]
            for name, changes in (('START8', {}), ('START10', {'ASSESSMENT_ENTRY_STARTUP_LIMIT': 10,
                    'ASSESSMENT_ADMISSION_CAPACITY': 10}), ('BURST32', {'ASSESSMENT_ENTRY_STARTUP_BURST': 32})):
                self.register(name, modified(submission_config, changes))
            startup_modes = ('login', 'startup', 'startup-mixed')
            rows = self.stage('startup', ('START8', 'START10', 'BURST32'), startup_modes)
            selection = select_candidate(rows, ('START8', 'START10', 'BURST32'), startup_modes)
            self.selections.append({'stage': 'startup', **selection})
            entry_config = self.configs[selection['candidate']][1]
            if not selection['qualified']:
                self.register('E19', modified(entry_config, {'ASSESSMENT_ENTRY_WORKERS': 19,
                    'ASSESSMENT_ENTRY_DB_CONNECTIONS': 15, 'ASSESSMENT_ENTRY_HEAVY_LIMIT': 18,
                    'ASSESSMENT_ENTRY_LOGIN_LIMIT': 16, 'ASSESSMENT_ENTRY_STARTUP_LIMIT': 12,
                    'ASSESSMENT_ADMISSION_CAPACITY': 12}))
                self.register('E15', entry_config)
                rows = self.stage('entry', ('E15', 'E19'), startup_modes)
                selection = select_candidate(rows, ('E15', 'E19'), startup_modes)
                self.selections.append({'stage': 'entry', **selection})
                entry_config = self.configs[selection['candidate']][1]
            self.register('REUSE0', modified(entry_config, {'DATABASE_CONN_MAX_AGE': 0}))
            self.register('REUSE60', modified(entry_config, {'DATABASE_CONN_MAX_AGE': 60}))
            reuse_modes = ('login-mixed', 'startup-mixed', 'submission')
            rows = self.stage('reuse', ('REUSE0', 'REUSE60'), reuse_modes)
            selection = select_candidate(rows, ('REUSE0', 'REUSE60'), reuse_modes)
            self.selections.append({'stage': 'reuse', **selection})
            final_config = self.configs[selection['candidate']][1]
            freshness_failed = any(not row.get('stats_ready') for row in self.results)
            if freshness_failed:
                self.register('STATS2', modified(final_config, {'ASSESSMENT_ANALYTICS_WORKERS': 2,
                    'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': 4}))
                rows = self.stage('statistics', ('STATS2',), ('submission', 'submission-mixed'))
                if all(row['passed'] for row in rows):
                    final_config = self.configs['STATS2'][1]
            self.register('selected', final_config)
            qualification = self.stage('qualification', ('selected',), ALL_MODES, counts=(180,))
            if not all_modes_passed(qualification):
                self.report.update(state='completed_with_failed_qualification',
                    failure_reason='At least one 180-person protocol mode failed; final repetition and capacity exploration withheld.')
                return
            final = self.stage('final', ('selected',), ALL_MODES, counts=(180,), rounds=2)
            if not all(row['passed'] for row in final):
                self.report.update(state='completed_with_failed_final', failure_reason='Repeated 180-person protocol qualification failed.')
                return
            self.report['protocol_180_passed_twice'] = True
            self.stage('exploration', ('selected',), ALL_MODES, counts=(240, 360))
            self.report['state'] = 'completed'
        except BaseException as error:
            self.report.update(state='safety_stopped', error_type=type(error).__name__,
                failure_reason=str(error) if isinstance(error, SafetyStop) else 'Controller exception; see private operator log.')
            raise
        finally:
            self.save_report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--diagnostic-manifest', required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,23}', args.run_id)
            or not hasattr(os, 'geteuid') or os.geteuid() != 0 or not ROOT.is_dir()):
        raise SystemExit('Root on the explicitly prepared capacity DUT and a unique bounded run-id required')
    matrix = Matrix(args.run_id, args.diagnostic_manifest)
    matrix.run()
    print(json.dumps({'state': matrix.report['state'], 'report': str(matrix.directory / 'matrix-report.json'),
        'formal_capacity_accepted': False}), flush=True)


if __name__ == '__main__':
    main()
