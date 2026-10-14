#!/usr/bin/env python3
"""Root-owned Linux adapter for the six isolated school diagnostic stages.

Prepare matched history once with --prepare before invoking the sweep. Ordinary
HTTP/latency/persistence failures retain evidence; exit 75 is a safety interrupt.
All raw manifests, attempts and logs stay inside the supplied private directory.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))
from school_diagnostic_sweep import SafetyInterrupted, write_once
from school_short_storm import checks as short_checks
from school_mixed_300 import THRESHOLDS, CAPTURE_P99_THRESHOLDS, start_gate
from capture_protocol import CONTRACT
from observe_school_runtime import BACKLOG_SQL
from school_native_db_probe import NativeProbeClient, config_hash, expected_identity, helper_hash, private_config, validate_calibration

PROJECT = 'kt-school-e2e'
DATABASE = 'kt_plantform-db-1'
OWNED_DATABASE = 'kt-school-e2e-db-1'
ISOLATED_POSTGRES_PROFILE = 'isolated-pg17-v1'
OWNED_POSTGRES_VOLUME = PROJECT + '_pool_postgres_data'
OWNED_POSTGRES_RESOURCES = {'memory_bytes': 4 * 1024**3, 'cpus': 0, 'cpu_shares': 3122}
OWNED_POSTGRES_SETTINGS = {'max_connections': 64, 'shared_buffers': 1024**3,
    'effective_cache_size': 3 * 1024**3, 'work_mem': 4 * 1024**2,
    'maintenance_work_mem': 128 * 1024**2, 'autovacuum_work_mem': 64 * 1024**2,
    'max_wal_size': 2 * 1024**3, 'checkpoint_completion_target': .9,
    'fsync': 'on', 'synchronous_commit': 'on', 'full_page_writes': 'on'}
BASE_URL = 'http://127.0.0.1:18080'
ROLES = ('web', 'student-read', 'teacher-read', 'scheduler', 'pgbouncer', 'nginx',
         'analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker')
REDIS_ROLE = 'redis-admission'
WORKERS = {'web': 12, 'student-read': 2, 'teacher-read': 1}
POOL_ALIASES = {'web': 'kt_core', 'student-read': 'kt_student', 'teacher-read': 'kt_teacher',
                'analytics-worker': 'kt_background', 'student-comparison-worker': 'kt_background',
                'publication-worker': 'kt_background', 'export-worker': 'kt_background'}
SIX_POOL_PROFILE = 'six-pools-180-v1'
SIX_WORKERS = {'web': 1, 'auth': 4, 'assessment': 24, 'capture': 4, 'student-read': 2, 'teacher-read': 1}
SIX_POOL_ALIASES = {**POOL_ALIASES, 'auth': 'kt_auth', 'assessment': 'kt_assessment', 'capture': 'kt_capture'}
SIX_MEMORY = {'web': 512 * 1024**2, 'auth': 1024**3, 'assessment': 2 * 1024**3,
              'capture': 1280 * 1024**2, 'student-read': 768 * 1024**2, 'teacher-read': 512 * 1024**2}
SIX_CPU_SHARES = {'web': 512, 'auth': 2048, 'assessment': 4096, 'capture': 2048,
                  'student-read': 512, 'teacher-read': 256}
STAGE_NAMES = ('short-3', 'short-50', 'short-150', 'short-300', 'baseline-300', 'mixed-300')


def validate_owned_postgres_settings(settings):
    version = settings.get('server_version_num')
    if (type(version) is not int or not 170000 <= version < 180000
            or any(settings.get(key) != value for key, value in OWNED_POSTGRES_SETTINGS.items())):
        raise SafetyInterrupted('Owned PostgreSQL 17 tuning or durability differs')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_evidence(path):
    """Incomplete output is failed evidence, with an explicit reason retained."""
    path = Path(path)
    if not path.exists():
        return {}, {'error_type': 'MissingEvidence', 'file': path.name}
    try:
        value = read(path)
        if not isinstance(value, dict):
            raise TypeError('Expected an evidence object')
        return value, None
    except (OSError, ValueError, TypeError) as error:
        return {}, {'error_type': type(error).__name__, 'file': path.name, 'size_bytes': path.stat().st_size}


def owned_test_ids(items, images, roles=None):
    roles = tuple(roles or ROLES)
    return [item['Id'] for item in items if
            item['Config'].get('Labels', {}).get('com.docker.compose.project') == PROJECT
            and item['Config'].get('Labels', {}).get('com.docker.compose.service') in roles
            and item['Image'] == images.get(item['Config']['Labels']['com.docker.compose.service'])]


class SafetyGuard:
    def __init__(self):
        self.production_failures = 0

    def update(self, sample):
        self.production_failures = 0 if sample.get('production_healthy') else self.production_failures + 1
        if sample.get('memory_available_bytes', 0) < 2 * 1024**3:
            return 'host_memory_below_2_gib'
        if sample.get('postgres_available') is not True:
            return 'shared_postgres_unavailable'
        if sample.get('disk_error'):
            return 'private_evidence_disk_error'
        if self.production_failures >= 3:
            return 'production_health_failed_three_times'
        return None


def score_stage(stage, http, observation, *, history_matched=True, baseline=None, baseline_bound=False):
    """Diagnostic thresholds only; never changes the existing promotion gate."""
    short = stage.startswith('short-')
    beginning = http.get('started', http.get('warmup_started_at', http.get('started_at', 0)))
    ending = http.get('ended', http.get('ended_at', float('inf')))
    checks = {
        'observer_covers_load': bool(observation) and observation.get('source') == 'docker_events'
            and observation.get('started_at', float('inf')) <= beginning and observation.get('ended_at', 0) >= ending,
        'zero_observed_oom': observation.get('oom_kills') == 0 and not observation.get('emergency_stop_reason'),
        'no_swap_growth': observation.get('swap_start_bytes') is not None and observation.get('swap_end_bytes') is not None
            and observation['swap_end_bytes'] <= observation['swap_start_bytes'],
        'server_samples_present': bool(observation.get('host_database_samples')),
        'observer_completed_without_error': observation.get('state') == 'completed' and not observation.get('error_type'),
    }
    if short:
        try:
            checks.update(short_checks(http))
        except (KeyError, TypeError, ValueError):
            checks['short_http_evidence_complete'] = False
    else:
        protocol = http.get('capture_protocol', {})
        metrics = http.get('metrics', {})
        checks.update({'full_1800_second_duration': http.get('duration_seconds', 0) >= 1800,
            '300_started_within_tail_bounds': start_gate(http),
            'all_300_completed': http.get('completed_student_count') == 300,
            'zero_workflow_failures': http.get('workflow_failures') == 0,
            'http_error_rate_below_0_1_percent': http.get('requests', 0) > 0 and http.get('errors', 1) / http['requests'] < .001,
            'protocol_complete': protocol.get('contract') == CONTRACT and protocol.get('completed_sessions') == 300
                and protocol.get('all_events_acknowledged') is True and all(protocol.get('scenarios', {}).get(kind, 0) >= 300
                    for kind in ('single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer',
                                 'ime_protocol_simulation', 'queued_replay_reload_protocol', 'writer_lease_reacquire',
                                 'required_check', 'hint_observed_protocol', 'authorized_feedback_protocol')),
            'matched_complete_history': history_matched is True})
        for metric, bound in THRESHOLDS.items():
            if stage == 'baseline-300' and metric.startswith('teacher_'):
                continue
            values = metrics.get(metric, {})
            checks[f'{metric}_p95'] = bool(values.get('count')) and values.get('p95_seconds', float('inf')) <= bound
        for metric, bound in CAPTURE_P99_THRESHOLDS.items():
            values = metrics.get(metric, {})
            checks[f'{metric}_p99'] = bool(values.get('count')) and values.get('p99_seconds', float('inf')) <= bound
        if stage == 'mixed-300':
            freshness = observation.get('teacher_freshness', {})
            checks.update({'five_teachers': http.get('teacher_count') == 5,
                           '25_scope_switches': http.get('scope_switches', 0) >= 25,
                           'five_completed_exports': http.get('exports_created', 0) >= 5 and http.get('exports_completed', 0) >= 5,
                           'same_bound_baseline_available': baseline_bound is True and bool(baseline),
                           'teacher_initial_data_ready_within_30_seconds': freshness.get('terminal_count') == 300
                               and freshness.get('initial_count') == 300 and freshness.get('initial_p95_seconds') is not None
                               and freshness['initial_p95_seconds'] <= 30,
                           'teacher_latest_source_ready_within_30_seconds': freshness.get('current_count') == 300
                               and freshness.get('latest_count') == 300 and freshness.get('latest_p95_seconds') is not None
                               and freshness['latest_p95_seconds'] <= 30})
            for metric in ('save_draft', 'submit_item'):
                before = (baseline or {}).get('metrics', {}).get(metric, {}).get('p95_seconds')
                after = metrics.get(metric, {}).get('p95_seconds')
                checks[f'{metric}_degradation_at_most_20_percent'] = (
                    isinstance(before, (float, int)) and before > 0 and isinstance(after, (float, int)) and after / before <= 1.2)
    return {'performance_passed': all(checks.values()), 'checks': checks, 'formal_capacity_accepted': False,
            'global_background_backlog_is_observation_only': True}


HISTORY_SCRIPT = '''
import json,time
from pathlib import Path
from school_short_storm import ProtocolShortRunner,StudentState,run_phases,summary
from capture_protocol import evidence_files
class HistoricalRunner(ProtocolShortRunner):
 def phase(self,name,states,operation,workers,*,synchronized=True):
  return super().phase(name,states,operation,25,synchronized=False)
fixture=json.load(open(MANIFEST))
runner=HistoricalRunner('http://127.0.0.1:18080',fixture['task_id'],35,item_interval=0)
runner.protocol_course_id,runner.protocol_experiment_id=fixture['course_id'],fixture['experiment_id']
runner.released_at=time.perf_counter()
states=[StudentState(name,fixture['password']) for name in fixture['usernames']]
with evidence_files(MANIFEST,OUTPUT) as files:
 files['runner']=runner;runner.attempt_output=files['attempts']
 run_phases(runner,states);runner.attempt_output=None
 result={'mode':'historical_fixture_preparation','formal_capacity_accepted':False,'concurrent_http_students':25,
  'metrics':{key:summary(values) for key,values in runner.metrics.items()},'errors':runner.errors,
  'capture_protocol':runner.protocol_summary(),'attempt_summary':runner.attempt_summary()}
 result['historical_workflow_complete']=not runner.errors and result['capture_protocol']['completed_sessions']==300
 files['complete']=result['historical_workflow_complete'];json.dump(result,files['result'],indent=2)
raise SystemExit(0 if result['historical_workflow_complete'] else 1)
'''

LONG_SCRIPT = '''
import json
from types import SimpleNamespace
from school_mixed_300 import phase
from school_short_storm import summary
from capture_protocol import evidence_files
args=SimpleNamespace(base_url='http://127.0.0.1:18080',phase=PHASE,timeout=35,duration=1800,teacher_interval=10)
with evidence_files('/attempt/fixture.json','/attempt/http.json') as files:
 result=phase(args,json.load(open('/attempt/fixture.json')),files)
 result['item_arrivals']=files['runner'].item_arrivals
 result['generator']={'start_send_delay':summary(files['runner'].send_delays),
  'item_arrival_delay_interpretation':'Cumulative schedule lag includes previous service waits; it is not pure client queueing.'}
 result['formal_capacity_accepted']=False
 json.dump(result,files['result'],indent=2)
raise SystemExit(0 if result['workflow_failures']==0 else 1)
'''


class DiagnosticRuntime:
    def __init__(self, config):
        self.config = config
        self.candidate = Path(config['candidate_dir'])
        self.private = Path(config['private_dir'])
        self.children = []
        self.load_name = None
        self.http_launched = False
        self.observer = None
        self.guard = SafetyGuard()
        self.postgres_guard = config.get('postgres_guard', 'docker')
        self.native_probe = NativeProbeClient(config['native_probe']) if config.get('native_probe') else None
        if self.postgres_guard not in ('docker', 'native'):
            raise SafetyInterrupted('Unknown PostgreSQL protection mode')
        if self.postgres_guard == 'native':
            try:
                certificate = read(config['probe_calibration'])
                self.validated_probe_binding = self.probe_binding()
                calibrated = self.native_probe is not None and validate_calibration(certificate, self.validated_probe_binding)
            except (OSError, ValueError, KeyError, TypeError):
                calibrated = False
            if not calibrated:
                raise SafetyInterrupted('Native protection requires bound paired calibration and fault tests')
        self.compose = ['docker', 'compose', '-p', PROJECT, '--env-file', str(self.private / 'school-test.env')]
        for name in ('docker-compose.yml', 'docker-compose.school.yml', 'docker-compose.school-test.yml', 'docker-compose.school-test-external-db.yml'):
            self.compose += ['-f', str(self.candidate / name)]
        self.compose += ['-f', config['compose_overlay_path']]
        if any(not re.fullmatch(r'sha256:[a-f0-9]{64}', config.get('images', {}).get(role, '')) for role in self.runtime_roles):
            raise SafetyInterrupted('Immutable image IDs required for every frozen role')
        _ = self.database_container

    def source_value(self):
        value = self.config['source_config']
        return read(value) if isinstance(value, str) else value

    @property
    def six_pool_profile(self):
        value = self.config['source_config']
        source = self.source_value() if isinstance(value, dict) or Path(value).exists() else {}
        profile = source.get('pool_profile')
        if profile not in (None, SIX_POOL_PROFILE):
            raise SafetyInterrupted('Unknown frozen pool profile')
        return profile == SIX_POOL_PROFILE

    @property
    def isolated_postgres(self):
        value = self.config['source_config']
        source = self.source_value() if isinstance(value, dict) or Path(value).exists() else {}
        profile = source.get('postgres_profile')
        if profile not in (None, ISOLATED_POSTGRES_PROFILE) or self.config.get('postgres_profile', profile) != profile:
            raise SafetyInterrupted('Unknown or inconsistent frozen PostgreSQL profile')
        if profile == ISOLATED_POSTGRES_PROFILE and any(source.get('roles', {}).get('db', {}).get(key) != value
                for key, value in OWNED_POSTGRES_RESOURCES.items()):
            raise SafetyInterrupted('Owned PostgreSQL resource binding differs')
        return profile == ISOLATED_POSTGRES_PROFILE

    @property
    def database_container(self):
        expected = OWNED_DATABASE if self.isolated_postgres else DATABASE
        if self.config.get('database_container', DATABASE) != expected:
            raise SafetyInterrupted('Only the designated frozen PostgreSQL container is permitted')
        return expected

    @property
    def database_host(self):
        expected = 'school-pool-postgres' if self.isolated_postgres else 'school-test-postgres'
        value = self.config['source_config']
        source = self.source_value() if isinstance(value, dict) or Path(value).exists() else {}
        if source.get('database_host', expected) != expected:
            raise SafetyInterrupted('Frozen PostgreSQL network alias differs')
        return expected

    @property
    def http_workers(self):
        return SIX_WORKERS if self.six_pool_profile else WORKERS

    @property
    def pool_aliases(self):
        return SIX_POOL_ALIASES if self.six_pool_profile else POOL_ALIASES

    @property
    def runtime_roles(self):
        source = self.source_value() if isinstance(self.config['source_config'], dict) or Path(self.config['source_config']).exists() else {}
        admission = source.get('admission', {})
        roles = ROLES + ('auth', 'assessment', 'capture') if self.six_pool_profile else ROLES
        if self.isolated_postgres:
            roles += ('db',)
        if REDIS_ROLE not in self.config['images'] and not admission.get('enabled'):
            return roles
        expected = source.get('roles', {}).get(REDIS_ROLE, {})
        task_ids = admission.get('task_ids')
        if (admission.get('enabled') is not True or not expected
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', self.config['images'].get(REDIS_ROLE, ''))
                or not isinstance(task_ids, list)
                or any(type(value) is not int or value <= 0 for value in task_ids)
                or len(set(task_ids)) != len(task_ids) or admission.get('capacity') != 8
                or expected.get('memory_bytes', 0) <= 0 or expected.get('cpus', 0) <= 0):
            raise SafetyInterrupted('Redis admission requires a frozen image, resources and exact task allowlist at capacity 8')
        return roles + (REDIS_ROLE,)

    def probe_binding(self):
        probe_config = self.config['native_probe']
        secret = Path(probe_config['secret_file'])
        secret_config = private_config(secret)
        return {'source_config_sha256': fingerprint(self.source_value()),
                'probe_config_sha256': fingerprint(probe_config),
                'probe_secret_sha256': config_hash(secret_config),
                'native_identity_sha256': config_hash(expected_identity(secret_config)),
                'helper_sha256': helper_hash(), 'database_container': self.database_container,
                'docker_deadline_seconds': 1, 'native_deadline_seconds': 1}

    def start_command(self):
        return self.compose + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', *self.runtime_roles]

    def direct_command(self, attempt, args, *, mounts=()):
        operation = PROJECT + '-diagnostic-op-' + fingerprint({'attempt': str(attempt), 'args': args})[:16]
        command = self.compose + ['run', '--rm', '--no-deps', '--pull=never', '--name', operation,
            '--label', 'school.diagnostic.operation=' + operation, '--entrypoint', 'python',
            '-e', 'POSTGRES_HOST=' + self.database_host, '-e', 'POSTGRES_PORT=5432',
            '-e', 'POSTGRES_DB=kt_e2e', '-e', 'POSTGRES_USER=kt_e2e_user', '-e', 'POSTGRES_POOL_ALIAS=',
            '-v', str(self.private) + ':/private:ro', '-v', str(attempt) + ':/attempt']
        for mount in mounts:
            command += ['-v', mount]
        return command + ['web', *args]

    @staticmethod
    def container_name(fixture_id):
        return PROJECT + '-diagnostic-' + hashlib.sha256(fixture_id.encode()).hexdigest()[:16]

    def load_command(self, attempt, fixture_id, args):
        name = self.container_name(fixture_id)
        return ['docker', 'run', '--rm', '--pull=never', '--network', 'host', '--memory', '512m', '--cpu-shares', '256',
                '--label', 'com.docker.compose.project=' + PROJECT, '--label', 'com.docker.compose.service=load',
                '--label', 'school.diagnostic.fixture=' + fixture_id, '--name', name,
                '-v', str(attempt) + ':/attempt', '--entrypoint', 'python', self.config['images']['web'], *args]

    def capture(self, command, timeout=30):
        return subprocess.check_output(command, cwd=self.candidate, text=True, stderr=subprocess.PIPE, timeout=timeout).strip()

    def run_private(self, command, attempt, label, timeout=300):
        with (attempt / f'{label}.log').open('x', encoding='utf-8') as stream:
            os.chmod(attempt / f'{label}.log', 0o600)
            process = subprocess.Popen(command, cwd=self.candidate, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            self.children.append(process)
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if command[:2] == ['docker', 'compose'] and '--name' in command:
                    self.stop_oneoff(command[command.index('--name') + 1])
                self.terminate_child(process)
                return 124
            except BaseException:
                if command[:2] == ['docker', 'compose'] and '--name' in command:
                    self.stop_oneoff(command[command.index('--name') + 1])
                self.terminate_child(process)
                raise

    def stop_oneoff(self, name):
        inspected = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True, timeout=10, check=False)
        if inspected.returncode:
            return
        items = json.loads(inspected.stdout)
        if len(items) != 1:
            raise SafetyInterrupted('One-off operation identity changed')
        item = items[0]
        labels = item['Config'].get('Labels', {})
        if (labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') != 'web'
                or labels.get('com.docker.compose.oneoff', '').lower() != 'true'
                or labels.get('school.diagnostic.operation') != name or item['Image'] != self.config['images']['web']):
            raise SafetyInterrupted('Refusing to stop an unowned one-off operation')
        subprocess.run(['docker', 'stop', '-t', '5', item['Id']], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, check=False)

    @staticmethod
    def terminate_child(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def inspect_test(self):
        ids = self.capture(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT]).split()
        return json.loads(self.capture(['docker', 'inspect', *ids])) if ids else []

    def validate_containers(self, items):
        roles = {}
        source = self.source_value() if isinstance(self.config['source_config'], dict) or Path(self.config['source_config']).exists() else {}
        for item in items:
            labels = item['Config'].get('Labels', {})
            role = labels.get('com.docker.compose.service')
            if labels.get('com.docker.compose.oneoff', '').lower() == 'true' or role == 'load':
                continue
            if labels.get('com.docker.compose.project') != PROJECT or role not in self.runtime_roles or role in roles:
                raise SafetyInterrupted('Runtime role identity mismatch')
            if not item['State']['Running'] or item['Image'] != self.config['images'][role]:
                raise SafetyInterrupted('Frozen runtime image or process changed')
            env = dict(value.split('=', 1) for value in item['Config'].get('Env', []) if '=' in value)
            if role not in ('nginx', REDIS_ROLE):
                if env.get('POSTGRES_DB') != 'kt_e2e' or env.get('POSTGRES_USER') != 'kt_e2e_user':
                    raise SafetyInterrupted('Isolated database identity mismatch')
            if role == REDIS_ROLE:
                expected_redis = source.get('roles', {}).get(REDIS_ROLE, {})
                networks = item.get('NetworkSettings', {}).get('Networks', {})
                if set(networks) != {PROJECT + '_default'} or item['HostConfig'].get('PortBindings'):
                    raise SafetyInterrupted('Redis admission must use the isolated network without host ports')
                if ('memory_bytes' not in expected_redis or item['HostConfig'].get('Memory') != expected_redis['memory_bytes']
                        or 'cpus' not in expected_redis or item['HostConfig'].get('NanoCpus') != round(expected_redis['cpus'] * 1e9)):
                    raise SafetyInterrupted('Frozen Redis admission resources changed')
            if role in self.pool_aliases and (env.get('POSTGRES_HOST') != 'pgbouncer' or env.get('POSTGRES_POOL_ALIAS') != self.pool_aliases[role]):
                raise SafetyInterrupted('Isolated pooled database alias mismatch')
            if role == 'scheduler' and (env.get('POSTGRES_HOST') != self.database_host or env.get('POSTGRES_POOL_ALIAS', '') != ''):
                raise SafetyInterrupted('Scheduler must use the direct isolated database')
            if role == 'pgbouncer' and env.get('PGBOUNCER_SERVER_HOST') != self.database_host:
                raise SafetyInterrupted('PgBouncer must point to the designated shared PostgreSQL alias')
            if role == 'db':
                networks = item.get('NetworkSettings', {}).get('Networks', {})
                mounts = item.get('Mounts', [])
                if (not self.isolated_postgres or item.get('Name') != '/' + self.database_container
                        or set(networks) != {PROJECT + '_default'}
                        or self.database_host not in networks[PROJECT + '_default'].get('Aliases', [])
                        or item['HostConfig'].get('PortBindings') or len(mounts) != 1
                        or mounts[0].get('Type') != 'volume' or mounts[0].get('Name') != OWNED_POSTGRES_VOLUME
                        or mounts[0].get('Destination') != '/var/lib/postgresql/data' or mounts[0].get('RW') is not True):
                    raise SafetyInterrupted('Owned PostgreSQL name, network, ports or volume differs')
            expected = source.get('roles', {}).get(role, {})
            if role in (self.http_workers if self.six_pool_profile else ('web', 'student-read')) and source.get('admission', {}).get('enabled') is True:
                admission = source['admission']
                expected_env = {
                    'ASSESSMENT_ADMISSION_ENABLED': '1',
                    'ASSESSMENT_ADMISSION_TASK_IDS': ','.join(str(value) for value in admission.get('task_ids', [])),
                    'ASSESSMENT_ADMISSION_CAPACITY': str(admission.get('capacity')),
                    'ASSESSMENT_ADMISSION_REDIS_URL': 'redis://redis-admission:6379/0',
                }
                if any(env.get(key) != value for key, value in expected_env.items()):
                    raise SafetyInterrupted('Admission environment binding differs on entry workers')
            if role in self.http_workers and (env.get('GUNICORN_WORKERS') != str(expected.get('workers', self.http_workers[role])) or
                    env.get('CAPTURE_V2_ENABLED') != '1' or env.get('CAPTURE_V2_COURSE_IDS') != '*'):
                raise SafetyInterrupted('Frozen capture-enabled worker configuration required')
            if 'memory_bytes' in expected and item['HostConfig']['Memory'] != expected['memory_bytes']:
                raise SafetyInterrupted('Frozen memory configuration changed')
            if 'cpus' in expected and item['HostConfig']['NanoCpus'] != round(expected['cpus'] * 1e9):
                raise SafetyInterrupted('Frozen CPU configuration changed')
            if 'cpu_shares' in expected and item['HostConfig'].get('CpuShares') != expected['cpu_shares']:
                raise SafetyInterrupted('Frozen CPU priority changed')
            if role == 'nginx' and item['HostConfig'].get('PortBindings', {}).get('80/tcp') != [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]:
                raise SafetyInterrupted('Only loopback diagnostic port 18080 is permitted')
            roles[role] = item
        if set(roles) != set(self.runtime_roles):
            raise SafetyInterrupted('Frozen diagnostic service set is incomplete')
        return roles

    def verify_runtime(self):
        self.validate_containers(self.inspect_test())
        postgres = json.loads(self.capture(['docker', 'inspect', self.database_container]))[0]
        expected_project = PROJECT if self.isolated_postgres else 'kt_plantform'
        if not postgres['State']['Running'] or postgres['Config'].get('Labels', {}).get('com.docker.compose.project') != expected_project:
            raise SafetyInterrupted('Designated shared PostgreSQL identity changed')
        network = postgres['NetworkSettings']['Networks'].get(PROJECT + '_default', {})
        if self.database_host not in network.get('Aliases', []):
            raise SafetyInterrupted('Explicit isolated PostgreSQL alias is missing')
        if self.isolated_postgres:
            volume = json.loads(self.capture(['docker', 'volume', 'inspect', OWNED_POSTGRES_VOLUME]))[0]
            if (volume.get('Name') != OWNED_POSTGRES_VOLUME
                    or (volume.get('Labels') or {}).get('com.docker.compose.project') != PROJECT):
                raise SafetyInterrupted('Owned PostgreSQL volume project identity differs')
            sql = "SELECT json_build_object('server_version_num',current_setting('server_version_num')::int"
            for key in OWNED_POSTGRES_SETTINGS:
                value = "current_setting('" + key + "')"
                if key in ('max_connections',):
                    value += '::int'
                elif key == 'checkpoint_completion_target':
                    value += '::numeric'
                elif key not in ('fsync', 'synchronous_commit', 'full_page_writes'):
                    value = 'pg_size_bytes(' + value + ')'
                sql += ",'" + key + "'," + value
            validate_owned_postgres_settings(self.db_json(sql + ')'))
            if self.native_probe is not None:
                secret = private_config(self.config['native_probe']['secret_file'])
                if expected_identity(secret)[3:5] != (network.get('IPAddress'), 5432):
                    raise SafetyInterrupted('Native probe must target the owned PostgreSQL identity')
        try:
            identity = self.db_json("SELECT json_build_object('database',current_database(),'owner',pg_get_userbyid(datdba)) FROM pg_database WHERE datname=current_database()")
        except (subprocess.SubprocessError, OSError) as error:
            raise SafetyInterrupted('Direct isolated PostgreSQL is unavailable') from error
        if identity != {'database': 'kt_e2e', 'owner': 'kt_e2e_user'}:
            raise SafetyInterrupted('Direct isolated PostgreSQL owner binding differs')

    def db_json(self, sql):
        output = self.capture(['docker', 'exec', self.database_container, 'sh', '-c',
            'exec psql -X -t -A -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kt_e2e -c "$1"', 'diagnostic-read-only',
            'BEGIN READ ONLY; ' + sql + '; COMMIT'], timeout=35)
        rows = [line for line in output.splitlines() if line.strip() not in ('BEGIN', 'COMMIT', '')]
        if len(rows) != 1:
            raise RuntimeError('Expected one read-only PostgreSQL JSON row')
        return json.loads(rows[0])

    def backlog(self, task_id=None):
        if task_id is None:
            return self.db_json(BACKLOG_SQL)
        task = int(task_id)
        return self.db_json(f"""SELECT json_build_object(
          'research', (SELECT count(*) FROM analytics_researchsessionmaterialization m JOIN assessment_experimentsession s ON s.id=m.session_id WHERE s.class_task_id={task} AND m.status IN ('queued','running')),
          'research_failed', (SELECT count(*) FROM analytics_researchsessionmaterialization m JOIN assessment_experimentsession s ON s.id=m.session_id WHERE s.class_task_id={task} AND m.status='failed'),
          'comparison', (SELECT count(*) FROM experiments_classexperimenttask t LEFT JOIN analytics_studentclasstaskcomparison c ON c.class_task_id=t.id WHERE t.id={task} AND t.result_revision>0 AND (c.id IS NULL OR c.status<>'ready' OR c.source_revision<>t.result_revision)))""")

    def safety_sample(self, attempt):
        try:
            available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
        except (OSError, StopIteration):
            available = 0
        try:
            with urllib.request.urlopen(self.config['production_health_url'], timeout=1) as response:
                healthy = response.status == 200
        except Exception:
            healthy = False
        ticket = self.native_probe.begin() if self.native_probe is not None else None
        probe_started = time.monotonic()
        probe = {'source': 'docker_exec_pg_isready', 'outcome': 'returncode', 'returncode': None,
                 'started_monotonic': probe_started, 'deadline_seconds': 1}
        try:
            probe['returncode'] = subprocess.run(['docker', 'exec', self.database_container, 'pg_isready', '-d', 'kt_e2e', '-U', 'kt_e2e_user'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1, check=False).returncode
        except subprocess.TimeoutExpired:
            probe['outcome'] = 'timeout'
        except OSError:
            probe['outcome'] = 'os_error'
        probe['ended_monotonic'] = time.monotonic()
        probe['elapsed_seconds'] = probe['ended_monotonic'] - probe_started
        native = self.native_probe.finish(ticket) if ticket is not None else None
        pg = probe['returncode'] == 0
        if self.postgres_guard == 'native':
            pg = (native is not None and native.get('outcome') == 'ready'
                  and native.get('deadline_seconds') == 1 and 0 <= native.get('total_seconds', 1) < 1
                  and native.get('helper_sha256') == self.validated_probe_binding['helper_sha256']
                  and native.get('probe_secret_sha256') == self.validated_probe_binding['probe_secret_sha256']
                  and native.get('identity_sha256') == self.validated_probe_binding['native_identity_sha256'])
        try:
            disk = os.statvfs(attempt)
            disk_error = disk.f_bavail == 0
        except OSError:
            disk_error = True
        sample = {'at': time.time(), 'memory_available_bytes': available, 'production_healthy': healthy,
                  'postgres_available': pg, 'postgres_probe': probe, 'postgres_guard': self.postgres_guard,
                  'native_postgres_probe': native, 'disk_error': disk_error}
        if self.native_probe is not None:
            path = attempt / 'database-probe.progress.json'
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps({key: sample[key] for key in
                    ('at', 'postgres_available', 'postgres_guard', 'postgres_probe', 'native_postgres_probe')}))
                temporary.chmod(0o600)
                os.replace(temporary, path)
            except OSError:
                sample['disk_error'] = True
        return sample

    def stop_owned(self):
        # A production container and the shared PostgreSQL can never enter this list.
        ids = owned_test_ids(self.inspect_test(), self.config['images'], self.runtime_roles)
        if ids:
            subprocess.run(['docker', 'stop', '-t', '10', *ids], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)

    def stop_load(self):
        if self.load_name:
            inspected = subprocess.run(['docker', 'inspect', self.load_name], text=True, capture_output=True, timeout=10, check=False)
            items = json.loads(inspected.stdout) if inspected.returncode == 0 else []
            if items and items[0]['Config'].get('Labels', {}).get('com.docker.compose.project') == PROJECT and items[0]['Config']['Labels'].get('com.docker.compose.service') == 'load' and items[0]['Image'] == self.config['images']['web']:
                subprocess.run(['docker', 'stop', '-t', '5', items[0]['Id']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        self.load_name = None

    def bind_attempt(self, attempt, create=False):
        source = self.source_value()
        if any(not re.fullmatch(r'[a-f0-9]{64}', source.get(key, '')) for key in ('source_sha256', 'frontend_sha256', 'config_sha256')):
            raise SafetyInterrupted('Source/frontend/config binding is incomplete')
        path = attempt / 'source-config.json'
        if create:
            write_once(path, source)
            write_once(attempt / 'runtime-binding.json', {'runtime_sha256': fingerprint(self.config), 'source_config': source})
        elif read(path) != source or read(attempt / 'runtime-binding.json')['runtime_sha256'] != fingerprint(self.config):
            raise SafetyInterrupted('Runtime or source binding changed during stage')
        return source

    def validate_seeded_pair(self, pair, run):
        if any(item['Config'].get('Labels', {}).get('com.docker.compose.service') == 'web'
                and item['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() == 'true'
                for item in self.inspect_test()):
            raise SafetyInterrupted('A one-off operation is still active; committed seed cannot be assumed')
        fixtures = {}
        for suffix in ('baseline', 'mixed', 'baseline-history', 'mixed-history'):
            path = pair / 'raw' / f'{run}-{suffix}.json'
            if not path.is_file():
                raise ValueError('Seed outputs are incomplete; partial fixtures cannot be reused')
            fixture = read(path)
            phase = suffix.split('-')[0]
            if (fixture.get('database') != 'kt_e2e' or fixture.get('capture_protocol') != CONTRACT
                    or fixture.get('student_count') != 300 or fixture.get('item_count') != 20
                    or fixture.get('run_id') != run + '-' + phase
                    or len(set(fixture.get('usernames', []))) != 300 or len(set(fixture.get('teacher_usernames', []))) != 5):
                raise SafetyInterrupted('Seed fixture roster or protocol binding is invalid')
            task, experiment, course, group = (int(fixture[key]) for key in ('task_id', 'experiment_id', 'course_id', 'class_id'))
            students = ','.join("'" + name.replace("'", "''") + "'" for name in fixture['usernames'])
            teachers = ','.join("'" + name.replace("'", "''") + "'" for name in fixture['teacher_usernames'])
            counts = self.db_json(f"""SELECT json_build_object(
              'mapping', (SELECT count(*) FROM experiments_classexperimenttask t JOIN experiments_experiment e ON e.id=t.experiment_id JOIN accounts_classgroup g ON g.id=t.class_group_id JOIN accounts_course c ON c.id=e.course_id WHERE t.id={task} AND e.id={experiment} AND g.id={group} AND c.id={course} AND g.course_id=c.id),
              'participants', (SELECT count(*) FROM experiments_experimentversionparticipantsnapshot p JOIN accounts_user u ON u.id=p.student_id WHERE p.class_task_id={task} AND u.username IN ({students})),
              'items', (SELECT count(*) FROM experiments_questionsnapshot q JOIN experiments_classexperimenttask t ON t.release_snapshot_id=q.version_id WHERE t.id={task}),
              'sessions', (SELECT count(*) FROM assessment_experimentsession WHERE class_task_id={task}),
              'roster', (SELECT count(*) FROM accounts_courseenrollment e JOIN accounts_user u ON u.id=e.student_id WHERE e.course_id={course} AND e.class_group_id={group} AND u.role='student' AND u.username IN ({students})),
              'teachers', (SELECT count(*) FROM accounts_user u WHERE u.role='teacher' AND u.username IN ({teachers})))""")
            if counts != {'mapping': 1, 'participants': 300, 'items': 20, 'sessions': 0, 'roster': 300, 'teachers': 5}:
                raise SafetyInterrupted('Seed transaction is absent, incomplete or its tasks are no longer cold')
            fixtures[suffix] = fixture
        for phase in ('baseline', 'mixed'):
            current, history = fixtures[phase], fixtures[phase + '-history']
            if any(current[key] != history[key] for key in ('course_id', 'class_id', 'usernames', 'teacher_usernames', 'password')):
                raise SafetyInterrupted('Prepared history and current rosters are not matched')

    def prepare(self, pair):
        pair = Path(pair)
        pair.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (pair / 'prepared.json').exists():
            report = read(pair / 'prepared.json')
            if report['runtime_sha256'] != fingerprint(self.config):
                raise SafetyInterrupted('Prepared history source binding differs')
            return report
        resumed = (pair / 'prepare-started.json').exists()
        if resumed:
            if read(pair / 'prepare-started.json').get('runtime_sha256') != fingerprint(self.config):
                raise SafetyInterrupted('Seed preparation binding changed; refusing resume')
            if any((pair / (phase + '.json')).exists() for phase in ('baseline', 'mixed', 'baseline-history', 'mixed-history')):
                raise ValueError('History preparation already began; only completed cold seeding can resume')
        else:
            write_once(pair / 'prepare-started.json', {'runtime_sha256': fingerprint(self.config), 'at': time.time()})
        self.verify_runtime()
        self.bind_attempt(pair, create=not resumed)
        run = 'dg-' + hashlib.sha256((self.config['run_id'] + str(pair)).encode()).hexdigest()[:16]
        password = '/private/' + Path(self.config['fixture_password_file']).relative_to(self.private).as_posix()
        if not resumed:
            code = self.run_private(self.direct_command(pair, ['manage.py', 'seed_school_mixed_load', '--capture-protocol',
                        '--run-id', run, '--password-file', password, '--output-dir', '/attempt/raw']), pair, 'seed-pair', 1800)
            if code:
                raise RuntimeError('Isolated matched fixture seeding failed')
        self.validate_seeded_pair(pair, run)
        source = self.source_value()
        histories = {}
        for phase in ('baseline', 'mixed'):
            for suffix in (phase, phase + '-history'):
                fixture = read(pair / 'raw' / f'{run}-{suffix}.json')
                fixture['source_config'] = source
                write_once(pair / (suffix + '.json'), fixture)
            history_path = pair / (phase + '-history.json')
            script = "import sys;sys.path.insert(0,'/app/scripts/load');MANIFEST=" + repr('/attempt/' + history_path.name)
            script += ';OUTPUT=' + repr('/attempt/' + phase + '-history-http.json') + '\n' + HISTORY_SCRIPT
            command = self.load_command(pair, run + '-' + phase + '-history', ['-c', script])
            code = self.monitored_process(command, pair, phase + '-history', timeout=6000)
            http, http_error = read_evidence(pair / (phase + '-history-http.json'))
            verify = self.run_private(self.direct_command(pair, ['manage.py', 'verify_school_protocol_load', '--manifest',
                    '/attempt/' + history_path.name, '--output', '/attempt/' + phase + '-history-persistence.json']), pair, phase + '-history-verify', 180)
            persistence, persistence_error = read_evidence(pair / (phase + '-history-persistence.json'))
            histories[phase] = {'process_exit_code': code, 'verification_exit_code': verify,
                'completed_sessions': http.get('capture_protocol', {}).get('completed_sessions', 0),
                'workflow_completed': http.get('historical_workflow_complete') is True,
                'data_integrity_passed': persistence.get('passed') is True, 'backlog': self.backlog(read(history_path)['task_id'])}
            histories[phase]['evidence_errors'] = {key: value for key, value in (('http', http_error), ('persistence', persistence_error)) if value}
        matched = all(row['workflow_completed'] and row['data_integrity_passed'] and row['completed_sessions'] == 300 for row in histories.values())
        report = {'runtime_sha256': fingerprint(self.config), 'histories': histories, 'matched_complete_history': matched,
                  'history_preparation_concurrency': 25, 'history_preparation_timeout_seconds': 6000,
                  'global_backlog': self.backlog(), 'formal_capacity_accepted': False}
        write_once(pair / 'prepared.json', report)
        return report

    def setup(self, stage, attempt, fixture_id):
        self.verify_runtime()
        self.bind_attempt(attempt, create=True)
        if stage.startswith('short-'):
            count = int(stage.split('-')[-1])
            run = 'dg-' + hashlib.sha256(fixture_id.encode()).hexdigest()[:16]
            script = "import json,os;from django.db import connection;from apps.experiments.management.commands.seed_school_short_load import Command;"
            script += "assert connection.settings_dict['NAME']=='kt_e2e';fixture=json.load(open('/anchor.json'));"
            script += "fixture['run_id']=" + repr(run) + ";json.dump(fixture,open('/attempt/short-anchor.json','x'));os.chmod('/attempt/short-anchor.json',0o600);"
            options = {'manifest': '/attempt/short-anchor.json', 'source_config': '/attempt/source-config.json',
                       'output_dir': '/attempt/seeded', 'capture_protocol': True}
            script += 'Command()._seed(' + repr(options) + ',sizes=(' + str(count) + ',))'
            code = self.run_private(self.direct_command(attempt, ['manage.py', 'shell', '-c', script],
                    mounts=(self.config['anchor_manifest'] + ':/anchor.json:ro',)), attempt, 'seed-short', 600)
            if code:
                return {'returncode': code, 'fixture_created': False}
            fixture = read(attempt / 'seeded' / f'short-{count}.json')
        else:
            pair = Path(self.config['pair_dir'])
            phase = stage.split('-')[0]
            claim = pair / (phase + '-stage-claim.json')
            if claim.exists():
                pair = self.private / 'diagnostic-pairs' / hashlib.sha256(fixture_id.encode()).hexdigest()[:20]
                claim = pair / (phase + '-stage-claim.json')
            prepared = self.prepare(pair)
            write_once(claim, {'fixture_id': fixture_id, 'attempt_dir': str(attempt)})
            fixture = read(pair / (phase + '.json'))
            cold = self.db_json('SELECT to_json(count(*)) FROM assessment_experimentsession WHERE class_task_id=' + str(int(fixture['task_id'])))
            if cold != 0:
                raise SafetyInterrupted('Long fixture already consumed; refusing old task reuse')
            write_once(attempt / 'history-binding.json', {'pair_dir': str(pair), 'matched_complete_history': prepared['matched_complete_history'],
                                                       'histories': prepared['histories']})
        write_once(attempt / 'fixture.json', fixture)
        return {'returncode': 0, 'fixture_created': True, 'task_id': fixture['task_id'], 'formal_capacity_accepted': False}

    def readiness(self, stage, attempt, fixture_id):
        self.bind_attempt(attempt)
        self.verify_runtime()
        with urllib.request.urlopen(BASE_URL + '/health/', timeout=5) as response:
            if response.status != 200:
                return {'returncode': 1, 'healthy': False}
        fixture = read(attempt / 'fixture.json')
        if fixture.get('database') != 'kt_e2e' or fixture.get('capture_protocol') != CONTRACT or fixture.get('source_config') != self.source_value():
            raise SafetyInterrupted('Dedicated fixture source binding is invalid')
        from school_short_storm import ShortRunner, StudentState
        runner = ShortRunner(BASE_URL, fixture['task_id'], 35)
        state = StudentState(fixture['usernames'][0], fixture['password'])
        try:
            runner.login(state)
        except Exception as error:
            write_once(attempt / 'authentication-attempts.json', runner.request_attempts)
            return {'returncode': 1, 'authenticated': False, 'error_type': type(error).__name__}
        write_once(attempt / 'authentication-attempts.json', runner.request_attempts)
        return {'returncode': 0, 'healthy': True, 'authenticated': True, 'actual_services': list(self.runtime_roles)}

    def monitored_process(self, command, attempt, label, timeout):
        self.load_name = command[command.index('--name') + 1]
        with (attempt / (label + '.log')).open('x', encoding='utf-8') as stream, \
             (attempt / (label + '-safety.jsonl')).open('x', encoding='utf-8') as safety:
            os.chmod(attempt / (label + '.log'), 0o600)
            os.chmod(attempt / (label + '-safety.jsonl'), 0o600)
            process = subprocess.Popen(command, cwd=self.candidate, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            self.children.append(process)
            self.http_launched = True
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    tick = time.monotonic()
                    sample = self.safety_sample(attempt)
                    safety.write(json.dumps(sample) + '\n'); safety.flush()
                    reason = self.guard.update(sample)
                    if self.observer is not None and self.observer.poll() is not None:
                        observed, _ = read_evidence(attempt / 'observation.json')
                        if observed.get('emergency_stop_reason'):
                            reason = observed['emergency_stop_reason']
                        elif self.observer.returncode != 0:
                            raise RuntimeError('Runtime observer failed during HTTP load')
                    if reason:
                        write_once(attempt / (label + '-safety-interruption.json'), {'reason': reason, 'sample': sample})
                        self.stop_load(); self.stop_owned()
                        raise SafetyInterrupted(reason)
                    if time.monotonic() >= deadline:
                        self.stop_load(); self.terminate_child(process)
                        return 124
                    time.sleep(max(0, 2 - (time.monotonic() - tick)))
                return process.returncode
            finally:
                self.stop_load()
                self.terminate_child(process)
                if self.native_probe is not None:
                    self.native_probe.close()

    def load(self, stage, attempt, fixture_id):
        self.bind_attempt(attempt)
        self.verify_runtime()
        write_once(attempt / 'backlog-before.json', {'global': self.backlog(), 'stage': self.backlog(read(attempt / 'fixture.json')['task_id'])})
        stop = attempt / 'observer.stop'
        log = (attempt / 'observer.log').open('x', encoding='utf-8')
        os.chmod(attempt / 'observer.log', 0o600)
        observer_command = [sys.executable, str(self.candidate / 'scripts/load/observe_school_runtime.py'), '--project', PROJECT,
            '--database-container', self.database_container, '--scope', 'short' if stage.startswith('short-') else 'formal',
            '--duration', '2700', '--stop-file', str(stop), '--output', str(attempt / 'observation.json')]
        if self.native_probe is not None:
            observer_command += ['--database-probe-progress', str(attempt / 'database-probe.progress.json')]
        observer = subprocess.Popen(observer_command,
            cwd=self.candidate, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close(); self.children.append(observer)
        self.observer = observer
        try:
            time.sleep(2)
            if observer.poll() is not None:
                raise RuntimeError('Runtime observer failed before HTTP load')
            if stage.startswith('short-'):
                args = ['scripts/load/school_short_storm.py', '--manifest', '/attempt/fixture.json', '--base-url', BASE_URL,
                        '--timeout', '35', '--output', '/attempt/http.json']
            else:
                script = "import sys;sys.path.insert(0,'/app/scripts/load');PHASE=" + repr(stage.split('-')[0]) + '\n' + LONG_SCRIPT
                args = ['-c', script]
            code = self.monitored_process(self.load_command(attempt, fixture_id, args), attempt, 'http-load',
                                          timeout=1700 if stage.startswith('short-') else 2600)
        finally:
            if not stop.exists():
                stop.touch(mode=0o600)
            try:
                observer.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.terminate_child(observer)
        http, http_error = read_evidence(attempt / 'http.json')
        workflow = http.get('checks', {}).get('complete_workflow') is True if stage.startswith('short-') else http.get('completed_student_count') == 300 and http.get('workflow_failures') == 0
        return {'returncode': code, 'process_exit_code': code, 'workflow_completed': workflow, 'load_executed': self.http_launched,
                'performance_passed': False, 'final_performance_scored_in_collect': True, 'observer_exit_code': observer.returncode,
                'http_result_present': bool(http), 'evidence_errors': {'http': http_error} if http_error else {}, 'formal_capacity_accepted': False}

    def collect(self, stage, attempt, fixture_id):
        self.bind_attempt(attempt)
        self.load_name = self.container_name(fixture_id)
        self.stop_load()
        since = str(int(read(attempt / 'started.json').get('started_at', time.time() - 3600))) if (attempt / 'started.json').exists() else '1h'
        self.run_private(self.compose + ['logs', '--no-color', '--since', since, *self.runtime_roles], attempt, 'services-private', 30)
        fixture = read(attempt / 'fixture.json') if (attempt / 'fixture.json').exists() else {}
        backlog = {'global': self.backlog(), 'stage': self.backlog(fixture['task_id']) if fixture else {}}
        before = read(attempt / 'backlog-before.json') if (attempt / 'backlog-before.json').exists() else {}
        backlog['global_delta'] = {key: value - before.get('global', {}).get(key, value) for key, value in backlog['global'].items()}
        write_once(attempt / 'backlog-after.json', backlog)
        http, http_error = read_evidence(attempt / 'http.json')
        observation, observation_error = read_evidence(attempt / 'observation.json')
        history = read(attempt / 'history-binding.json') if (attempt / 'history-binding.json').exists() else {}
        baseline = None; baseline_bound = False
        if stage == 'mixed-300':
            baseline_root = attempt.parents[1] / 'baseline-300'
            for path in sorted(baseline_root.glob('attempt-*/http.json'), reverse=True):
                binding = read(path.parent / 'runtime-binding.json')
                if binding.get('runtime_sha256') == fingerprint(self.config):
                    baseline, baseline_error = read_evidence(path)
                    baseline_bound = baseline_error is None
                    if baseline_bound:
                        break
        result = score_stage(stage, http, observation, history_matched=history.get('matched_complete_history', False),
                             baseline=baseline, baseline_bound=baseline_bound)
        result.update(returncode=0 if observation else 1, stage_backlog=backlog['stage'], global_backlog_delta=backlog['global_delta'])
        result['evidence_errors'] = {key: value for key, value in (('http', http_error), ('observation', observation_error)) if value}
        if result['evidence_errors']:
            result['performance_passed'] = False
        write_once(attempt / 'diagnostic-score.json', result)
        return result

    def reconcile(self, stage, attempt, fixture_id):
        self.bind_attempt(attempt)
        if not (attempt / 'fixture.json').exists() or not (attempt / 'fixture.ledger.json').exists():
            return {'returncode': 1, 'data_integrity_passed': False, 'reason': 'manifest_or_protocol_ledger_missing'}
        command = self.direct_command(attempt, ['manage.py', 'verify_school_protocol_load', '--manifest', '/attempt/fixture.json',
                                              '--output', '/attempt/persistence.json'])
        code = self.run_private(command, attempt, 'persistence-private', 240)
        evidence, error = read_evidence(attempt / 'persistence.json')
        return {'returncode': code, 'data_integrity_passed': code == 0 and evidence.get('passed') is True,
                'checks': evidence.get('checks', {}), 'evidence_error': error, 'formal_capacity_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--stage', choices=STAGE_NAMES)
    parser.add_argument('--step', choices=('setup', 'readiness', 'load', 'collect', 'reconcile'))
    parser.add_argument('--attempt-dir')
    parser.add_argument('--fixture-id')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Authenticated root-owned Linux runtime required')
    config_path = Path(args.runtime)
    if config_path.is_symlink() or config_path.stat().st_uid != 0 or config_path.stat().st_mode & 0o077:
        raise SystemExit('Root-private runtime manifest required')
    runtime = DiagnosticRuntime(read(config_path))
    def interrupted(signum, frame):
        raise SafetyInterrupted('Runtime operation interrupted by signal')
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    attempt = None
    try:
        if args.start:
            startup = runtime.private / ('startup-' + runtime.config['run_id'])
            startup.mkdir(mode=0o700)
            code = runtime.run_private(runtime.start_command(), startup, 'start-services', 300)
            if code:
                raise RuntimeError('Fixed isolated service startup failed')
            runtime.verify_runtime()
            print(json.dumps({'started': True, 'production_cutover_performed': False}))
            return
        if args.prepare:
            report = runtime.prepare(runtime.config['pair_dir'])
            print(json.dumps({'prepared': True, 'matched_complete_history': report['matched_complete_history']}))
            return
        if not all((args.stage, args.step, args.attempt_dir, args.fixture_id)):
            raise ValueError('Stage, step, unique fixture and attempt directory required')
        if Path(args.attempt_dir).is_symlink():
            raise SafetyInterrupted('Symlink evidence directory is forbidden')
        attempt = Path(args.attempt_dir).resolve()
        if not attempt.is_relative_to(runtime.private.resolve()) or not attempt.is_dir() or attempt.is_symlink():
            raise SafetyInterrupted('Evidence must remain in the designated private directory')
        if not re.fullmatch('[A-Za-z0-9_-]+', args.fixture_id):
            raise ValueError('Unique diagnostic fixture identifier required')
        result = getattr(runtime, args.step)(args.stage, attempt, args.fixture_id)
    except SafetyInterrupted as error:
        result = {'returncode': 75, 'safety_interrupted': True, 'error_type': type(error).__name__, 'load_executed': runtime.http_launched}
    except Exception as error:
        result = {'returncode': 1, 'error_type': type(error).__name__, 'performance_passed': False, 'data_integrity_passed': False,
                  'load_executed': runtime.http_launched}
    finally:
        runtime.stop_load()
        for child in runtime.children:
            runtime.terminate_child(child)
        if runtime.native_probe is not None:
            runtime.native_probe.close()
    if attempt is not None:
        write_once(attempt / (args.step + '-result.json'), result)
    print(json.dumps({'stage': args.stage, 'step': args.step, **result}))
    raise SystemExit(result['returncode'])


if __name__ == '__main__':
    main()
