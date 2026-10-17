import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from tests.test_student_capacity_deployment import CAPACITY, CAPACITY_V3


PROJECT = 'kt-school-e2e-capacity'
SOURCE = {'pool_profile': 'shared-entry-v2', 'postgres_profile': 'isolated-pg17-v1'}


def runtime():
    rows = []
    for role, count, workers in (('entry', 1, 15), ('assessment', 1, 8), ('capture', 1, 3),
            ('web', 1, 1), ('teacher-read', 1, 1), ('session-submission-worker', 4, None),
            ('analytics-worker', 1, None), ('scheduler', 1, None)):
        for index in range(count):
            env = CAPACITY | ({'GUNICORN_WORKERS': str(workers)} if workers else {})
            rows.append({'Id': f'{role}-{index}', 'State': {'Running': True, 'Pid': 1},
                'Config': {'Env': [f'{key}={value}' for key, value in env.items()],
                    'Labels': {'com.docker.compose.project': PROJECT, 'com.docker.compose.service': role}}})
    return rows


class CapacityObserverTests(SimpleTestCase):
    def test_container_inspection_leaves_budget_for_one_transient_retry(self):
        module = self.module()
        with patch.object(module.time, 'monotonic', return_value=10):
            module.begin_sample_budget()
        with patch.object(module.time, 'monotonic', return_value=12), \
                patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps([{'Id': 'a' * 64}]))) as command:
            module.inspect_containers(['a' * 64])
        self.assertEqual(command.call_args.kwargs['timeout'], 5)

    def test_cli_budget_bounds_each_call_and_total_sample_work(self):
        module = self.module()
        with patch.object(module.time, 'monotonic', return_value=10):
            module.begin_sample_budget()
        with patch.object(module.time, 'monotonic', return_value=33), \
                patch.object(module.subprocess, 'check_output', return_value='') as command:
            module.docker_output(['docker', 'ps'], text=True)
        self.assertLessEqual(command.call_args.kwargs['timeout'], 2)
        with patch.object(module.time, 'monotonic', return_value=36), self.assertRaises(TimeoutError):
            module.docker_output(['docker', 'ps'], text=True)

    def test_transient_retry_keeps_original_twenty_five_second_deadline(self):
        module = self.module()
        ticks = [10]
        with patch.object(module.time, 'monotonic', side_effect=lambda: ticks[0]):
            module.begin_sample_budget()
            ticks[0] = 34
            def timeout(*args, **kwargs):
                self.assertEqual(kwargs['timeout'], 1)
                ticks[0] = 35
                raise module.subprocess.TimeoutExpired('docker', 1)
            with patch.object(module.subprocess, 'check_output', side_effect=timeout) as query, \
                    self.assertRaisesRegex(TimeoutError, 'budget exhausted'):
                module.docker_output(['docker', 'ps'], text=True)
        self.assertEqual(query.call_count, 1)

    def test_unchanged_container_list_refreshes_identity_each_sample(self):
        module = self.module()
        cache = {}
        item = {'Id': 'a' * 64}
        with patch.object(module.subprocess, 'check_output', return_value=item['Id']), \
                patch.object(module, 'inspect_containers', return_value=([item], [])) as inspect:
            with patch.object(module.time, 'monotonic', return_value=100):
                module.discover_runtime(cache, project=PROJECT)
            with patch.object(module.time, 'monotonic', return_value=101):
                module.discover_runtime(cache, project=PROJECT)
            self.assertEqual(inspect.call_count, 2)
            with patch.object(module.time, 'monotonic', return_value=110):
                module.discover_runtime(cache, project=PROJECT)
            self.assertEqual(inspect.call_count, 3)

    def test_slow_sample_is_not_completed_even_without_a_missing_metric(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            result = {'host_database_samples': [{'state': 'failed', 'sample_duration_seconds': 37}]}
            module.finish_observation(output, result, None)
            self.assertEqual(json.loads(output.read_text())['state'], 'failed')

    def test_discovery_timeout_preserves_failed_phase_without_a_successful_sample(self):
        module = self.module()
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            (output.parent / 'source-config.json').write_text('{}')
            args = SimpleNamespace(project=PROJECT, duration=1, output=str(output),
                database_container='db', stop_file=None, abort_memory_below_mb=0)
            events = Mock()
            events.poll.return_value = 0
            with patch.object(module.subprocess, 'check_output', side_effect=['web', 'db']), \
                    patch.object(module.subprocess, 'Popen', return_value=events), \
                    patch.object(module, 'recent_oom_events', return_value=[]), \
                    patch.object(module, 'memory_available_bytes', return_value=8 * 1024**3), \
                    patch.object(module, 'discover_runtime', side_effect=module.subprocess.TimeoutExpired('PRIVATE', 5)), \
                    self.assertRaises(module.subprocess.TimeoutExpired):
                result = {}
                module.observe(args, result)
            progress = json.loads(output.with_suffix('.progress.json').read_text())
            self.assertIsNone(progress['latest_sample'])
            self.assertEqual(progress['current_sample']['state'], 'failed')
            self.assertEqual(progress['current_sample']['phase'], 'container_discovery')
            self.assertEqual(result['host_database_samples'], [])
            self.assertNotIn('PRIVATE', output.with_suffix('.samples.jsonl').read_text())

    def module(self):
        path = settings.BASE_DIR / 'scripts/load/observe_school_runtime.py'
        spec = importlib.util.spec_from_file_location('capacity_observer', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_consumers_are_required_even_without_source_running_claims(self):
        module = self.module()
        self.assertEqual(module.observer_http_roles(SOURCE), {'entry', 'assessment', 'capture', 'web', 'teacher-read'})
        self.assertTrue({'session-submission-worker', 'analytics-worker', 'scheduler', 'db'} <= module.observer_required_roles(SOURCE))

    def test_actual_replicas_and_connection_sum_are_checked(self):
        module = self.module()
        result = module.capacity_runtime_budget(runtime(), SOURCE, PROJECT)
        self.assertEqual(result['http_workers'], 28)
        self.assertEqual(result['ordinary_connections'], 30)
        self.assertEqual(result['planned_total_connections'], 46)
        self.assertEqual(result['available_nonreserved_connections'], 61)
        self.assertEqual(result['submission_replicas'], 4)
        self.assertEqual(result['analytics_replicas'], 1)
        self.assertEqual(result['pools']['kt_submission'], {'pool_size': 4, 'reserve_pool_size': 0})
        for remove in ('session-submission-worker-3', 'analytics-worker-0', 'scheduler-0'):
            with self.subTest(remove=remove), self.assertRaisesRegex(ValueError, 'replica'):
                module.capacity_runtime_budget([row for row in runtime() if row['Id'] != remove], SOURCE, PROJECT)
        oneoff = runtime()[-1]
        oneoff['Config']['Labels']['com.docker.compose.oneoff'] = 'True'
        with self.assertRaisesRegex(ValueError, 'replica'):
            module.capacity_runtime_budget(runtime()[:-1] + [oneoff], SOURCE, PROJECT)

    def test_configuration_drift_and_over_budget_runtime_fail_closed(self):
        module = self.module()
        rows = runtime()
        rows[0]['Config']['Env'].append('ASSESSMENT_ENTRY_DB_CONNECTIONS=15')
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            module.capacity_runtime_budget(rows, SOURCE, PROJECT)
        rows = runtime()
        for row in rows:
            row['Config']['Env'].append('SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS=40')
        with self.assertRaisesRegex(ValueError, 'connection budget'):
            module.capacity_runtime_budget(rows, SOURCE, PROJECT)
        self.assertIsNone(module.capacity_runtime_budget([], {'pool_profile': 'shared-entry-v1'}, PROJECT))

    def test_v2_checks_measured_database_pool_allocations(self):
        module = self.module()
        budget = module.capacity_runtime_budget(runtime(), SOURCE, PROJECT)
        names = tuple(budget['pools'])
        pools = 'database|cl_waiting\n' + '\n'.join(f'{name}|0' for name in names)
        stats = 'database|total_query_count\n' + '\n'.join(f'{name}|1' for name in names)
        database_rows = ['name|pool_size|reserve_pool_size'] + [f'{name}|{row["pool_size"]}|{row["reserve_pool_size"]}'
            for name, row in budget['pools'].items()]
        with patch.object(module.subprocess, 'check_output', side_effect=['pooler', pools, stats, '\n'.join(database_rows)]):
            measured = module.pool_metrics(PROJECT, SOURCE, capacity=budget)
        self.assertEqual(measured['kt_submission']['configured_pool_size'], 4)
        bad = '\n'.join(database_rows).replace('kt_submission|4|0', 'kt_submission|1|0')
        with patch.object(module.subprocess, 'check_output', side_effect=['pooler', pools, stats, bad]), self.assertRaisesRegex(ValueError, 'kt_submission'):
            module.pool_metrics(PROJECT, SOURCE, capacity=budget)


    def test_v3_observes_dynamic_workers_reserves_and_database_ceiling(self):
        module = self.module()
        rows = runtime()
        rows = [row for row in rows if not row['Id'].startswith('session-submission-worker-')
                or row['Id'] == 'session-submission-worker-0']
        for row in rows:
            role = row['Config']['Labels']['com.docker.compose.service']
            env = CAPACITY_V3 | {'ASSESSMENT_CORE_WORKERS': '2', 'ASSESSMENT_TEACHER_WORKERS': '3',
                'ASSESSMENT_CORE_DB_CONNECTIONS': '2', 'ASSESSMENT_TEACHER_DB_CONNECTIONS': '3',
                'ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS': '2', 'SCHOOL_DB_MAX_CONNECTIONS': '160'}
            workers = {'entry': 15, 'assessment': 12, 'capture': 3, 'web': 2, 'teacher-read': 3}.get(role)
            if workers:
                env['GUNICORN_WORKERS'] = str(workers)
            row['Config']['Env'] = [f'{key}={value}' for key, value in env.items()]
        budget = module.capacity_runtime_budget(rows, SOURCE | {'pool_profile': 'shared-entry-v3'}, PROJECT)
        self.assertEqual(budget['http_workers'], 35)
        self.assertEqual(budget['ordinary_connections'], 30)
        self.assertEqual(budget['pool_reserve_connections'], 5)
        self.assertEqual(budget['available_nonreserved_connections'], 157)
        self.assertEqual(budget['pools']['kt_core'], {'pool_size': 2, 'reserve_pool_size': 2})
        self.assertIn('analytics-worker', module.observer_required_roles({'pool_profile': 'shared-entry-v3'}))

    def test_cgroup_metrics_record_effective_cpuset_and_ancestor_constraints(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / 'slice'
            group = parent / 'container'
            group.mkdir(parents=True)
            for folder in (root, parent, group):
                (folder / 'cpu.max').write_text('max 100000')
                (folder / 'memory.max').write_text('max')
                (folder / 'cpuset.cpus.effective').write_text('0-7')
            (parent / 'cpu.max').write_text('200000 100000')
            (parent / 'memory.max').write_text('4096')
            for name, contents in {'cpu.stat': 'usage_usec 1', 'memory.events': 'oom 0',
                    'memory.current': '512', 'io.stat': ''}.items():
                (group / name).write_text(contents)
            result = module.cgroup_metrics(group, root=root)
            self.assertEqual(result['cpuset_cpus_effective'], '0-7')
            self.assertEqual(result['effective_cpu_quota_cores'], 2)
            self.assertEqual(result['effective_memory_max_bytes'], 4096)
            self.assertEqual(len(result['ancestry_constraints']), 3)


    def test_v3_pool_measurement_checks_actual_client_limit(self):
        module = self.module()
        capacity = {'pools': {'kt_entry': {'pool_size': 11, 'reserve_pool_size': 0}},
            'max_client_connections': 200}
        # Only pool visibility needed by this direct fixture; all seven aliases are required.
        aliases = ('kt_entry', 'kt_core', 'kt_assessment', 'kt_capture', 'kt_teacher', 'kt_background', 'kt_submission')
        pools = 'database|cl_waiting\n' + '\n'.join(f'{alias}|0' for alias in aliases)
        stats = 'database|total_query_count\n' + '\n'.join(f'{alias}|1' for alias in aliases)
        databases = 'name|pool_size|reserve_pool_size\nkt_entry|11|0'
        with patch.object(module.subprocess, 'check_output', side_effect=[
                'pooler', pools, stats, databases, 'key|value\nmax_client_conn|200']):
            result = module.pool_metrics(PROJECT, {'pool_profile': 'shared-entry-v3'}, capacity=capacity)
        self.assertEqual(result['kt_entry']['configured_max_client_connections'], 200)
        with patch.object(module.subprocess, 'check_output', side_effect=[
                'pooler', pools, stats, databases, 'key|value\nmax_client_conn|100']), \
                self.assertRaisesRegex(ValueError, 'client'):
            module.pool_metrics(PROJECT, {'pool_profile': 'shared-entry-v3'}, capacity=capacity)
