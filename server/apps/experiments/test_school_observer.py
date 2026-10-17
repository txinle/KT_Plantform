import importlib.util
import json
import tempfile
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase


class SchoolObserverTests(SimpleTestCase):
    def test_discovery_scopes_project_and_explicit_siblings_and_refreshes_identity(self):
        module = self.module()
        cache = {}
        with patch.object(module, 'docker_output', return_value='owned'), \
                patch.object(module, 'inspect_containers', side_effect=[
                    ([{'Id': 'owned', 'State': {'Pid': 1}}], []),
                    ([{'Id': 'owned', 'State': {'Pid': 2}}], [])]) as inspect:
            module.discover_runtime(cache, project='kt-school-e2e', extra_ids=['database'])
            items, _, age = module.discover_runtime(cache, project='kt-school-e2e', extra_ids=['database'])
        self.assertEqual(items[0]['State']['Pid'], 2)
        self.assertEqual(age, 0)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(inspect.call_args.args[0], ['owned', 'database'])

    def test_discovery_timeout_retries_once_without_refreshing_cache_on_failure(self):
        module = self.module()
        cache = {'items': [{'Id': 'old'}]}
        module.begin_sample_budget()
        with patch.object(module.subprocess, 'check_output', side_effect=[
                module.subprocess.TimeoutExpired('docker', 5), 'owned']) as query, \
                patch.object(module, 'inspect_containers', return_value=([{'Id': 'owned'}], [])):
            module.discover_runtime(cache, project='kt-school-e2e')
        self.assertEqual(query.call_count, 2)
        self.assertIn('label=com.docker.compose.project=kt-school-e2e', query.call_args.args[0])
        before = deepcopy(cache)
        with patch.object(module.subprocess, 'check_output', side_effect=module.subprocess.TimeoutExpired('docker', 5)), \
                self.assertRaises(module.subprocess.TimeoutExpired):
            module.discover_runtime(cache, project='kt-school-e2e')
        self.assertEqual(cache, before)

    def test_partial_sample_preserves_complete_sample_age_and_identity(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'runtime.json'
            complete = {'state': 'sampled', 'at': 20, 'sample_started_at': 19}
            partial = {'state': 'partial', 'at': 30, 'sample_started_at': 30,
                'resource_rows': [{'container_id': 'a', 'identity_confirmed_at': 19}]}
            module.publish_progress(output, 10, complete)
            module.publish_progress(output, 10, partial)
            result = json.loads(output.with_suffix('.progress.json').read_text())
            self.assertEqual(result['latest_sample'], complete)
            self.assertEqual(result['last_complete_sample_at'], 20)
            self.assertEqual(result['current_sample'], partial)
            self.assertEqual(result['freshness_clock'], 'monotonic')
            self.assertIsInstance(result['heartbeat_monotonic'], (int, float))
            self.assertEqual(len(output.with_suffix('.samples.jsonl').read_text().splitlines()), 2)

    def test_known_cgroup_partial_evidence_never_calls_docker_or_claims_fresh_identity(self):
        module = self.module()
        item = {'Id': 'a' * 64, 'State': {'Pid': 42}, 'Config': {'Labels': {'com.docker.compose.service': 'entry'}}}
        with patch.object(module, 'cgroup_path', return_value=Path('/known')), \
                patch.object(module, 'cgroup_metrics', return_value={'working_set_bytes': 123}), \
                patch.object(module, 'memory_available_bytes', return_value=456), \
                patch.object(module, 'host_cpu_counters', return_value={'run_queue': 2}), \
                patch.object(module, 'docker_output') as docker:
            row = module.partial_sample({'items': [item], 'confirmed_at': 19}, 30)
        docker.assert_not_called()
        self.assertEqual(row['state'], 'partial')
        self.assertEqual(row['resource_rows'][0]['identity_confirmed_at'], 19)
        self.assertEqual(row['resource_rows'][0]['cgroup']['working_set_bytes'], 123)
        self.assertEqual(row['memory_available_bytes'], 456)

    def test_host_partial_is_persisted_before_docker_blockage(self):
        module = self.module()
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'runtime.json'
            (output.parent / 'source-config.json').write_text('{}')
            args = SimpleNamespace(project='kt-school-e2e', duration=1, output=str(output),
                database_container='db', stop_file=None, abort_memory_below_mb=2)
            def blocked(*args):
                progress = json.loads(output.with_suffix('.progress.json').read_text())
                self.assertEqual(progress['current_sample']['state'], 'partial')
                self.assertEqual(progress['current_sample']['memory_available_bytes'], 4000000)
                raise module.subprocess.TimeoutExpired('docker', 5)
            with patch.object(module.subprocess, 'check_output', side_effect=['web', 'full-db']), \
                    patch.object(module.subprocess, 'Popen', return_value=Mock(poll=Mock(return_value=0))), \
                    patch.object(module, 'memory_available_bytes', return_value=4000000), \
                    patch.object(module, 'host_cpu_counters', return_value={}), \
                    patch.object(module, 'recent_oom_events', side_effect=blocked), \
                    patch.object(module.time, 'time', return_value=100), \
                    self.assertRaises(module.subprocess.TimeoutExpired):
                module.observe(args, {})

    def test_shared_entry_roles_replace_split_roles_and_keep_dependencies(self):
        module = self.module()
        source = {'pool_profile': 'shared-entry-v1', 'postgres_profile': 'isolated-pg17-v1', 'roles': {
            'startup': {'actual_running': True}, 'redis-session': {}, 'redis-admission': {},
            'session-submission-worker': {'actual_running': True}}}
        http = {'entry', 'assessment', 'capture', 'web', 'teacher-read'}
        self.assertEqual(module.observer_http_roles(source), http)
        self.assertEqual(module.observer_required_roles(source), http | {
            'db', 'scheduler', 'nginx', 'pgbouncer', 'redis-session', 'redis-admission',
            'session-submission-worker'})

    def test_shared_entry_pool_measurement_requires_every_pool_in_both_queries(self):
        module = self.module()
        aliases = ('kt_entry', 'kt_core', 'kt_assessment', 'kt_capture', 'kt_teacher',
                   'kt_background', 'kt_submission')
        source = {'pool_profile': 'shared-entry-v1'}
        def pools(names):
            return 'database|cl_waiting|sv_idle|maxwait\n' + '\n'.join(f'{name}|2|3|4' for name in names)
        def stats(names):
            return 'database|total_query_count|total_wait_time\n' + '\n'.join(f'{name}|5|6000' for name in names)
        with patch.object(module.subprocess, 'check_output', side_effect=[
                'pooler', pools((*aliases, 'kt_auth', 'unrelated')), stats(aliases)]):
            result = module.pool_metrics('kt-school-e2e', source)
        self.assertEqual(set(result), set(aliases))
        self.assertEqual(result['kt_entry']['cl_waiting'], 2)
        self.assertEqual(result['kt_entry']['total_wait_time'], 6000)
        for missing_from in ('pools', 'stats'):
            with self.subTest(missing_from=missing_from), patch.object(module.subprocess, 'check_output', side_effect=[
                    'pooler', pools(aliases[1:] if missing_from == 'pools' else aliases),
                    stats(aliases[1:] if missing_from == 'stats' else aliases)]), self.assertRaisesRegex(ValueError, 'kt_entry'):
                module.pool_metrics('kt-school-e2e', source)

    def test_cli_accepts_one_second_sampling_without_changing_default(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module.sys, 'argv', ['observer', '--project', 'kt-school-e2e',
                    '--interval', '1', '--output', str(Path(directory) / 'observation.json')]), \
                    patch.object(module, 'observe') as observe, patch('builtins.print'):
                module.main()
        self.assertEqual(observe.call_args.args[0].interval, 1)

    def test_batch_inspection_uses_one_command_and_full_container_identities(self):
        module = self.module()
        identities = ['a' * 64, 'b' * 64]
        rows = [{'Id': identity} for identity in identities]
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(rows))) as inspect, \
                patch.object(module, 'reinspect_container') as retry:
            items, lifecycle = module.inspect_containers(identities)
        inspect.assert_called_once_with(['docker', 'inspect', *identities],
                                       text=True, capture_output=True, timeout=5)
        retry.assert_not_called()
        self.assertEqual(items, rows)
        self.assertEqual(lifecycle, [])

    def test_partial_batch_reinspects_only_missing_ids_and_records_removal(self):
        module = self.module()
        identities = ['a' * 64, 'b' * 64, 'c' * 64]
        first, recovered = {'Id': identities[0]}, {'Id': identities[2]}
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout=json.dumps([first]))), \
                patch.object(module, 'reinspect_container', side_effect=[None, recovered]) as retry:
            items, lifecycle = module.inspect_containers(identities)
        self.assertEqual([call.args[0] for call in retry.call_args_list], identities[1:])
        self.assertEqual(items, [first, recovered])
        self.assertEqual(lifecycle, [{'status': 'skipped_removed_during_discovery',
                                     'container_id': identities[1]}])

    def test_batch_failure_without_missing_ids_and_identity_substitution_fail_closed(self):
        module = self.module()
        identity = 'a' * 64
        for exit_code, rows in ((1, [{'Id': identity}]), (0, [{'Id': 'b' * 64}]),
                                (0, [{'Id': identity}, {'Id': identity}])):
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=exit_code, stdout=json.dumps(rows))), self.assertRaises(ValueError):
                module.inspect_containers([identity])

    def test_missing_batch_identity_does_not_hide_real_reinspection_failure(self):
        module = self.module()
        with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='[]')), \
                patch.object(module, 'reinspect_container', side_effect=ValueError('Runtime container reinspection failed')), \
                self.assertRaisesMessage(ValueError, 'Runtime container reinspection failed'):
            module.inspect_containers(['a' * 64])

    def test_pool_metrics_keep_transaction_totals_and_server_transition_states(self):
        module = self.module()
        pools = ('database|cl_waiting|sv_active|sv_idle|sv_used|sv_tested|sv_login|maxwait\n'
                 'kt_assessment|2|3|4|5|6|7|8\n')
        stats = ('database|total_xact_count|total_xact_time|total_query_count|total_query_time|total_wait_time\n'
                 'kt_assessment|91|92000|93|94000|95000\n')
        with patch.object(module.subprocess, 'check_output', side_effect=['pooler', pools, stats]):
            result = module.pool_metrics('kt-school-e2e')['kt_assessment']
        self.assertEqual({key: result.get(key) for key in ('sv_used', 'sv_tested', 'sv_login')},
                         {'sv_used': 5, 'sv_tested': 6, 'sv_login': 7})
        self.assertEqual(result.get('total_xact_count'), 91)
        self.assertEqual(result.get('total_xact_time'), 92000)
        self.assertEqual(result['total_query_time'], 94000)

    def test_host_run_queue_and_process_counts_are_measured(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'stat').write_text('cpu 10 2 30 40 5 6 7 8 9 10\nprocs_running 3\nprocs_blocked 2\n')
            (root / 'loadavg').write_text('0.50 0.60 0.70 4/120 123\n')
            counters = module.host_cpu_counters(root)
        self.assertEqual(counters.get('procs_running'), 3)
        self.assertEqual(counters.get('procs_blocked'), 2)
        self.assertEqual(counters.get('run_queue'), 4)
        self.assertEqual(counters['system_ticks'], 30)
        self.assertEqual(counters['idle_ticks'], 40)

    def test_host_io_snapshot_keeps_device_counters_before_docker(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diskstats').write_text('8 0 sda 1 0 2 3 4 0 5 6 7 8 9\n')
            row = module.host_io_counters(root)['sda']
        self.assertEqual(row['read_bytes'], 1024)
        self.assertEqual(row['write_bytes'], 2560)
        self.assertEqual(row['io_in_progress'], 7)
        self.assertEqual(row['io_milliseconds'], 8)

    def test_owned_capacity_memory_guard_needs_three_fifteen_second_samples_and_resets(self):
        module = self.module()
        state = {}
        total = 32 * 1024**3
        available = 4 * 1024**3  # above 2GiB, below 15 percent
        self.assertFalse(module.capacity_memory_stop(state, 0, available, total))
        self.assertFalse(module.capacity_memory_stop(state, 14, available, total))
        self.assertFalse(module.capacity_memory_stop(state, 15, available, total))
        self.assertTrue(module.capacity_memory_stop(state, 30, available, total))
        self.assertFalse(module.capacity_memory_stop(state, 45, 8 * 1024**3, total))
        self.assertFalse(module.capacity_memory_stop(state, 60, available, total))
        self.assertEqual(state['low_samples'], 1)

    def test_failed_metric_is_missing_and_keeps_sanitized_error_and_timing(self):
        module = self.module()
        status = {}
        def fail():
            raise ValueError('PRIVATE SQL AND PASSWORD')
        with patch.object(module.time, 'time', side_effect=[100, 100.4]), \
                patch.object(module.time, 'monotonic', side_effect=[10, 10.4]):
            value = module.measure_metric('pgbouncer', fail, status)
        self.assertIsNone(value)
        self.assertEqual(status['pgbouncer']['state'], 'missing')
        self.assertEqual(status['pgbouncer']['error_type'], 'ValueError')
        self.assertEqual(status['pgbouncer']['started_at'], 100)
        self.assertEqual(status['pgbouncer']['ended_at'], 100.4)
        self.assertAlmostEqual(status['pgbouncer']['duration_seconds'], 0.4)
        self.assertNotIn('PRIVATE', json.dumps(status))

    def test_default_interval_is_two_seconds_and_cli_accepts_two(self):
        module = self.module()
        self.assertEqual(module.sample_delay(120, 100), 2)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            with patch.object(module.sys, 'argv', ['observer', '--project', 'kt-school-e2e',
                    '--interval', '2', '--output', str(output)]), \
                    patch.object(module, 'observe') as observe, patch('builtins.print'):
                module.main()
            self.assertEqual(observe.call_args.args[0].interval, 2)

    def test_pool_metrics_include_auth_assessment_and_capture_without_other_databases(self):
        module = self.module()
        pools = 'database|cl_active|cl_waiting|sv_active|sv_idle|maxwait\n' + '\n'.join(
            f'{alias}|4|{waiting}|3|1|2' for alias, waiting in (
                ('kt_core', 0), ('kt_auth', 2), ('kt_assessment', 7), ('kt_capture', 1), ('unrelated', 9)))
        stats = 'database|total_query_count|total_wait_time|avg_wait_time\n' + '\n'.join(
            f'{alias}|12|3000|250' for alias in ('kt_core', 'kt_auth', 'kt_assessment', 'kt_capture', 'unrelated'))
        with patch.object(module.subprocess, 'check_output', side_effect=['owned-pooler', pools, stats]):
            result = module.pool_metrics('kt-school-e2e')
        self.assertEqual(set(result), {'kt_core', 'kt_auth', 'kt_assessment', 'kt_capture'})
        self.assertEqual(result['kt_assessment']['cl_waiting'], 7)
        self.assertEqual(result['kt_auth']['cl_waiting'], 2)
        self.assertEqual(result['kt_capture']['cl_waiting'], 1)
        self.assertEqual(result['kt_assessment']['total_wait_time'], 3000)

    def test_six_pool_roles_and_owned_database_are_required_and_budgeted(self):
        module = self.module()
        source = {'pool_profile': 'six-pools-180-v1', 'postgres_profile': 'isolated-pg17-v1', 'roles': {}}
        self.assertEqual(module.observer_http_roles(source), {'web', 'auth', 'assessment', 'capture', 'student-read', 'teacher-read'})
        self.assertTrue({'db', 'auth', 'assessment', 'capture'} <= module.observer_required_roles(source))
        owned = {'Id': 'db', 'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e', 'com.docker.compose.service': 'db'}}}
        production = {'Id': 'prod-db', 'Config': {'Labels': {'com.docker.compose.project': 'kt_plantform', 'com.docker.compose.service': 'db'}}}
        self.assertEqual(module.observer_resource_scope(owned, source, 'kt-school-e2e', ['db']), 'isolated_test')
        self.assertEqual(module.observer_resource_scope(production, source, 'kt-school-e2e', ['db']), 'production_or_other')
        self.assertEqual(module.observer_resource_scope(owned, {}, 'kt-school-e2e', ['db']), 'shared_postgres')
        source['roles']['db'] = {'cgroup': {'memory_events': {'oom_kill': 1}}}
        self.assertFalse(module.new_oom(owned, {'memory_events': {'oom_kill': 1}}, source, 'kt-school-e2e', ['db']))
        self.assertTrue(module.new_oom(owned, {'memory_events': {'oom_kill': 2}}, source, 'kt-school-e2e', ['db']))

    def test_pool_metrics_preserve_startup_and_submission_waiting_and_stats(self):
        module = self.module()
        pools = ('database|cl_active|cl_waiting|sv_active|sv_idle|maxwait\n'
                 'kt_startup|2|3|1|0|4\nkt_submission|1|5|1|0|6\nunrelated|9|9|9|9|9\n')
        stats = ('database|total_xact_count|total_query_count|total_wait_time|avg_wait_time\n'
                 'kt_startup|11|12|13000|140\nkt_submission|21|22|23000|240\nunrelated|99|99|99|99\n')
        with patch.object(module.subprocess, 'check_output', side_effect=['owned-pooler', pools, stats]) as commands:
            result = module.pool_metrics('kt-school-e2e-prepared-20261015')
        self.assertEqual(set(result), {'kt_startup', 'kt_submission'})
        self.assertEqual(result['kt_startup']['cl_waiting'], 3)
        self.assertEqual(result['kt_startup']['total_xact_count'], 11)
        self.assertEqual(result['kt_startup']['total_wait_time'], 13000)
        self.assertEqual(result['kt_submission']['cl_waiting'], 5)
        self.assertEqual(result['kt_submission']['total_query_count'], 22)
        self.assertEqual(result['kt_submission']['avg_wait_time'], 240)
        self.assertIn('SHOW POOLS', commands.call_args_list[1].args[0][-1])
        self.assertIn('SHOW STATS', commands.call_args_list[2].args[0][-1])

    def test_prepared_roles_are_monitored_only_when_explicitly_running(self):
        module = self.module()
        source = {'pool_profile': 'six-pools-180-v1', 'roles': {
            'startup': {'actual_running': True}, 'session-submission-worker': {'actual_running': True}}}
        http = {'web', 'auth', 'assessment', 'startup', 'capture', 'student-read', 'teacher-read'}
        self.assertEqual(module.observer_http_roles(source), http)
        self.assertEqual(module.observer_required_roles(source),
                         http | {'scheduler', 'nginx', 'pgbouncer', 'session-submission-worker'})

    def test_non_running_prepared_roles_preserve_legacy_six_pool_sets(self):
        module = self.module()
        http = {'web', 'auth', 'assessment', 'capture', 'student-read', 'teacher-read'}
        for role_state in ({}, {'actual_running': False}, {'actual_running': None},
                           {'actual_running': 1}, {'actual_running': 'true'}):
            with self.subTest(role_state=role_state):
                source = {'pool_profile': 'six-pools-180-v1', 'roles': {
                    'startup': role_state, 'session-submission-worker': role_state}}
                self.assertEqual(module.observer_http_roles(source), http)
                self.assertEqual(module.observer_required_roles(source), http | {'scheduler', 'nginx', 'pgbouncer'})

    def test_host_cpu_steal_and_role_listen_queues_are_raw_bounded_counters(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'stat').write_text('cpu  10 2 30 40 5 6 7 8 9 10\ncpu0 1 2\n')
            (root / 'loadavg').write_text('0.50 0.60 0.70 1/120 123\n')
            network = root / '123/net'
            network.mkdir(parents=True)
            (network / 'tcp').write_text('header\n 0: 00000000:1F40 00000000:0000 0A 00000000:00000003 00:0 0 0\n 1: 00000000:1538 00000000:0000 0A 00000000:00000009 00:0 0 0\n')
            counters = module.host_cpu_counters(root)
            self.assertEqual(counters['steal_ticks'], 8)
            self.assertEqual(counters['iowait_ticks'], 5)
            queues = module.http_listen_queues(123, root)
            self.assertEqual(queues, [{'port': 8000, 'tx_queue': 0, 'rx_queue': 3}])

    def test_oom_events_include_removed_load_and_shared_database_before_cleanup(self):
        module = self.module()
        rows = [
            {'Actor': {'ID': 'removed-load', 'Attributes': {'com.docker.compose.project': 'kt-school-e2e'}}},
            {'Actor': {'ID': 'shared-db', 'Attributes': {'com.docker.compose.project': 'production'}}},
            {'Actor': {'ID': 'other', 'Attributes': {'com.docker.compose.project': 'production'}}},
        ]
        with patch.object(module.subprocess, 'check_output', return_value='\n'.join(json.dumps(row) for row in rows)) as query:
            found = module.recent_oom_events('kt-school-e2e', ['shared-db'], 100, 110)
        self.assertEqual(found, rows[:2])
        self.assertIn('100', query.call_args.args[0])
        self.assertIn('110', query.call_args.args[0])

    def test_sampling_stops_for_removed_load_oom_before_cgroup_inspection(self):
        module = self.module()
        from unittest.mock import Mock
        events = Mock()
        events.poll.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            (output.parent / 'source-config.json').write_text('{}')
            args = SimpleNamespace(project='kt-school-e2e', duration=1, output=str(output),
                                   database_container='db', stop_file=None)
            oom = {'Actor': {'ID': 'removed-load'}}
            with patch.object(module.subprocess, 'check_output', side_effect=['web', 'full-db']), \
                    patch.object(module.subprocess, 'Popen', return_value=events), \
                    patch.object(module, 'swap_used_bytes', return_value=0), \
                    patch.object(module, 'recent_oom_events', return_value=[oom]), \
                    patch.object(module, 'stop_test_project') as stop, \
                    patch.object(module.time, 'time', return_value=100):
                result = {}
                with self.assertRaisesMessage(SystemExit, 'OOM occurred'):
                    module.observe(args, result)
            stop.assert_called_once_with('kt-school-e2e', ['full-db'])
            self.assertEqual(result['oom_events'], [oom])
            self.assertEqual(result['emergency_stop_reason'], 'docker_oom_event')

    def test_emergency_stop_includes_late_load_container_but_preserves_shared_database(self):
        module = self.module()
        from unittest.mock import Mock
        items = {
            'web': {'Id': 'web', 'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e', 'com.docker.compose.service': 'web'}}},
            'late-load': {'Id': 'late-load', 'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e'}}},
            'database': {'Id': 'database', 'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e', 'com.docker.compose.service': 'db'}}},
            'other': {'Id': 'other', 'Config': {'Labels': {'com.docker.compose.project': 'production', 'com.docker.compose.service': 'web'}}},
        }
        with patch.object(module.subprocess, 'check_output', return_value='web\nlate-load\ndatabase\nother'), \
                patch.object(module, 'reinspect_container', side_effect=lambda identity: items[identity]), \
                patch.object(module.subprocess, 'run') as stop:
            module.stop_test_project('kt-school-e2e', ['database'])
        self.assertEqual(stop.call_args.args[0], ['docker', 'stop', '--time', '3', 'web', 'late-load'])

    def test_oom_guard_uses_increment_and_never_attributes_other_production_oom_to_test(self):
        module = self.module()
        def item(project, role):
            return {'Id': role, 'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': role}}}
        baseline = {'roles': {'web': {'cgroup': {'memory_events': {'oom_kill': 1}}}},
                    'shared_postgres_cgroup': {'memory_events': {'oom_kill': 2}}}
        self.assertFalse(module.new_oom(item('kt-school-e2e', 'web'), {'memory_events': {'oom_kill': 1}}, baseline, 'kt-school-e2e', ['db']))
        self.assertTrue(module.new_oom(item('kt-school-e2e', 'web'), {'memory_events': {'oom_kill': 2}}, baseline, 'kt-school-e2e', ['db']))
        self.assertTrue(module.new_oom(item('production', 'db'), {'memory_events': {'oom_kill': 3}}, baseline, 'kt-school-e2e', ['db']))
        self.assertFalse(module.new_oom(item('production', 'web'), {'memory_events': {'oom_kill': 4}}, baseline, 'kt-school-e2e', ['db']))

    def test_real_sampling_loop_uses_full_container_identities_and_finishes(self):
        module = self.module()
        for stopped, exit_code, six, pool_failure, missing_role in (
                (False, 0, False, False, False), (True, 143, False, False, False),
                (False, 143, False, False, False), (False, 0, True, False, False),
                (False, 0, 'shared', False, False),
                (False, 0, False, 'recover', False), (False, 0, True, False, True),
                (False, 0, False, 'memory', False), (False, 0, False, 'oom', False)):
            roles = ['web', 'student-read', 'teacher-read', 'scheduler', 'nginx', 'pgbouncer']
            if six:
                roles += ['auth', 'assessment', 'capture']
            if six == 'shared':
                roles = ['web', 'entry', 'assessment', 'capture', 'teacher-read', 'scheduler', 'nginx', 'pgbouncer']
            roles += ['db']
            items = {str(i + 1) * 64: {'Id': str(i + 1) * 64, 'Name': '/' + role,
                     'State': {'Running': True, 'Pid': i + 100},
                     'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e',
                                          'com.docker.compose.service': role}}}
                     for i, role in enumerate(roles)}
            identities = list(items)

            def docker(args, **kwargs):
                if args[1] == 'ps':
                    if missing_role and any(str(arg).startswith('id=') for arg in args):
                        return ''
                    selected = identities[:-1] if '--filter' in args else identities
                    return '\n'.join(selected if '--no-trunc' in args else [identity[:12] for identity in selected])
                if args[1] == 'inspect':
                    if '--format' in args:
                        return identities[-1]
                    selected = [items[next(full for full in identities if full.startswith(identity))]
                                for identity in args[2:]]
                    return json.dumps([item for item in selected
                                       if not missing_role or item['Config']['Labels']['com.docker.compose.service'] != 'capture'])
                if args[1] == 'exec':
                    return '[]' if args[-1] == module.DATABASE_WAIT_SQL else '{}'
                if args[1] == 'events':
                    return ''
                raise AssertionError(args)

            kernel = {'working_set_bytes': 1024, 'memory_current_bytes': 1024, 'memory_max': '4096',
                      'cpu': {'usage_usec': 10}}
            from unittest.mock import Mock
            events = Mock(returncode=exit_code)
            events.communicate.return_value = ('', '')
            events.poll.side_effect = [None, exit_code] if stopped else [exit_code]
            ticks = [100]
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'observation.json'
                source = {'pool_profile': 'six-pools-180-v1', 'postgres_profile': 'isolated-pg17-v1'} if six else {}
                if six == 'shared':
                    source['pool_profile'] = 'shared-entry-v1'
                (output.parent / 'source-config.json').write_text(json.dumps(source))
                meminfo = output.parent / 'meminfo'
                meminfo.write_text('MemTotal: 1048576 kB\n')
                stop = output.parent / 'stop'

                def advance(_):
                    ticks[0] = 100.5 if pool_failure and ticks[0] == 100 else 102
                    if stopped:
                        stop.touch()

                args = SimpleNamespace(project='kt-school-e2e', duration=1, output=str(output), scope='short',
                                       database_container='db', abort_memory_below_mb=2, stop_file=str(stop) if stopped else None)
                with patch.object(module.subprocess, 'check_output', side_effect=docker), \
                        patch.object(module.subprocess, 'run', side_effect=lambda args, **kwargs: SimpleNamespace(returncode=1 if missing_role else 0, stdout=docker(args), stderr='')), \
                        patch.object(module.subprocess, 'Popen', return_value=events), \
                        patch.object(module, 'cgroup_path', return_value=Path(directory)), \
                        patch.object(module, 'cgroup_metrics', return_value=kernel), \
                        patch.object(module, 'swap_used_bytes', return_value=0), \
                        patch.object(module, 'memory_available_bytes',
                                     side_effect=[500 * 1024 ** 2, 1] if pool_failure == 'memory' else None,
                                     return_value=500 * 1024 ** 2) as memory_probe, \
                        patch.object(module, 'recent_oom_events',
                                     side_effect=[[], [{'private': 'oom'}]] if pool_failure == 'oom' else None,
                                     return_value=[]) as oom_probe, \
                        patch.object(module, 'stop_test_project') as stop_project, \
                        patch.object(module, 'pool_metrics', side_effect=[module.subprocess.TimeoutExpired(
                            'PRIVATE SQL AND PASSWORD', 5), {}] if pool_failure else None, return_value={}) as pool_probe, \
                        patch.object(module, 'host_cpu_counters', return_value={'steal_ticks': 0}), \
                        patch.object(module, 'host_io_counters', return_value={}), \
                        patch.object(module, 'http_listen_queues', return_value=[]) as queues, \
                        patch.object(module, 'budget', return_value={'combined_working_set_bytes': 7168, 'checks': {'host_headroom': True, 'combined_working_set_plus_margin': True}}), \
                        patch.object(module, 'Path', side_effect=lambda path: meminfo if str(path) == '/proc/meminfo' else Path(path)), \
                        patch.object(module.time, 'time', side_effect=lambda: ticks[0]), \
                        patch.object(module.time, 'sleep', side_effect=advance):
                    result = {}
                    if missing_role:
                        with self.assertRaisesMessage(ValueError, 'Required database or runtime role missing'):
                            module.observe(args, result)
                        self.assertEqual(result['host_database_samples'], [])
                        continue
                    if pool_failure:
                        if pool_failure in ('memory', 'oom'):
                            with self.assertRaises(SystemExit):
                                module.observe(args, result)
                            stop_project.assert_called_once_with(args.project, [identities[-1]])
                        else:
                            module.observe(args, result)
                            stop_project.assert_not_called()
                            self.assertEqual(len(result['host_database_samples']), 2)
                            self.assertEqual(result['host_database_samples'][1]['state'], 'sampled')
                            self.assertEqual(memory_probe.call_count, 2)
                        self.assertEqual(oom_probe.call_count, 2)
                        sample = result['host_database_samples'][0]
                        self.assertIsNone(sample['pgbouncer'])
                        self.assertEqual(sample['measurement_status']['pgbouncer']['state'], 'missing')
                        self.assertEqual(sample['measurement_status']['pgbouncer']['error_type'], 'TimeoutExpired')
                        journal = output.with_suffix('.samples.jsonl').read_text()
                        complete_rows = [json.loads(line) for line in journal.splitlines()
                            if json.loads(line)['state'] != 'partial']
                        self.assertEqual(complete_rows[0]['state'], 'failed')
                        self.assertNotIn('PRIVATE', journal)
                        module.finish_observation(str(output), result, None)
                        final = json.loads(output.read_text())
                        self.assertEqual(final['state'], 'failed')
                        self.assertEqual(final['missing_measurements'], {'pgbouncer': 1})
                        self.assertEqual(final['missing_measurement_count'], 1)
                        self.assertEqual(final['failed_sample_count'], 1)
                        self.assertFalse(final['formal_capacity_accepted'])
                        continue
                    if exit_code and not stopped:
                        with self.assertRaises(SystemExit):
                            module.observe(args, result)
                        self.assertEqual(result['oom_observer_exit_code'], exit_code)
                        self.assertFalse(result['oom_observer_stopped_by_coordinator'])
                        continue
                    module.observe(args, result)
                self.assertEqual(len(result['host_database_samples']), 1)
                self.assertEqual(len(result['host_database_samples'][0]['resource_rows']), len(roles))
                self.assertEqual(queues.call_count, 5 if six == 'shared' else 6 if six else 3)
                pool_probe.assert_called_once_with(args.project, source)
                sample = result['host_database_samples'][0]
                self.assertEqual(sample['state'], 'sampled')
                self.assertEqual(sample['measurement_status']['pgbouncer']['state'], 'measured')
                self.assertEqual(sample['sample_started_at'], 100)
                self.assertIsInstance(sample['sample_started_monotonic'], (int, float))
                self.assertGreaterEqual(sample['sample_duration_seconds'], 0)
                self.assertGreaterEqual(sample['container_discovery_duration_seconds'], 0)
                if six:
                    self.assertEqual(next(row for row in sample['resource_rows'] if row['role'] == 'db')['scope'], 'isolated_test')
                    self.assertEqual(sample['database_wal']['scope'], 'isolated_postgres_instance')
                    self.assertNotIn('shared_wal', sample)
                self.assertEqual(result['oom_kills'], 0)
                self.assertEqual(result['stopped_by_coordinator'], stopped)
                self.assertFalse(result['teacher_freshness']['measured'])

    def test_main_preserves_normal_and_failed_lifecycle_and_refuses_replay(self):
        for failure in (False, True):
            module = self.module()
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'observation.json'
                args = SimpleNamespace(project='kt-school-e2e', duration=1, output=str(output), scope='short')

                def observed(options, result):
                    result['host_database_samples'] = [{'at': 1}, {'at': 2}]
                    for sample in result['host_database_samples']:
                        module.publish_progress(options.output, 0, sample)
                    if failure:
                        raise RuntimeError('private final query failed')

                with patch.object(module.argparse.ArgumentParser, 'parse_args', return_value=args), \
                        patch.object(module, 'observe', side_effect=observed), patch('builtins.print'):
                    if failure:
                        with self.assertRaises(RuntimeError):
                            module.main()
                    else:
                        module.main()
                    original = output.read_bytes()
                    with self.assertRaises(SystemExit):
                        module.main()
                    self.assertEqual(output.read_bytes(), original)
                self.assertEqual(json.loads(original)['state'], 'failed' if failure else 'completed')
                self.assertEqual(len(output.with_suffix('.samples.jsonl').read_text().splitlines()), 2)
                self.assertNotIn('private', original.decode())

    def module(self):
        spec = importlib.util.spec_from_file_location('school_observer',
            Path(settings.BASE_DIR) / 'scripts/load/observe_school_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cgroup_metrics_keep_throttle_memory_and_io_without_identity(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            group = Path(temporary)
            (group / 'cpu.stat').write_text('usage_usec 10\nnr_periods 2\nnr_throttled 1\nthrottled_usec 5\n')
            (group / 'memory.current').write_text('1024\n')
            (group / 'memory.swap.current').write_text('512\n')
            (group / 'memory.events').write_text('oom 0\noom_kill 0\n')
            (group / 'io.stat').write_text('8:0 rbytes=123 wbytes=456 rios=1 wios=2\n')
            row = module.cgroup_metrics(group)
        self.assertEqual(row['cpu']['nr_throttled'], 1)
        self.assertEqual(row['memory_current_bytes'], 1024)
        self.assertEqual(row['memory_swap_current_bytes'], 512)
        self.assertEqual(row['io'][0]['wbytes'], 456)
        self.assertEqual(row['memory_events']['oom_kill'], 0)

    def test_database_wait_sampling_excludes_queries_and_student_identifiers(self):
        module = self.module()
        self.assertIn('wait_event_type', module.DATABASE_WAIT_SQL)
        self.assertIn("datname='kt_e2e'", module.DATABASE_WAIT_SQL)
        self.assertNotIn('query,', module.DATABASE_WAIT_SQL)
        self.assertNotIn('usename', module.DATABASE_WAIT_SQL)

    def test_intermediate_diagnostics_survive_workload_failure_without_claiming_acceptance(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'short-observation.json'
            module.publish_progress(output, 10, {'state': 'sampled', 'at':20, 'database_waits':{'active':1}, 'pgbouncer':{}, 'memory_budget':{'checks':{'host_headroom':True}}})
            import json
            row=json.loads(output.with_suffix('.progress.json').read_text())
            self.assertEqual(row['state'], 'observing')
            self.assertFalse(row['formal_capacity_accepted'])
            self.assertEqual(row['latest_sample']['database_waits']['active'], 1)

    def test_every_periodic_sample_survives_later_failure(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            module.publish_progress(output, 10, {'at': 20, 'database_waits': []})
            module.publish_progress(output, 10, {'at': 30, 'database_waits': []})
            samples = [json.loads(line) for line in output.with_suffix('.samples.jsonl').read_text().splitlines()]
            self.assertEqual([row['at'] for row in samples], [20, 30])
            self.assertFalse(output.exists())

    def test_cpu_and_memory_samples_use_kernel_deltas_without_docker_stats(self):
        module = self.module()
        kernel = {'cpu': {'usage_usec': 5000000}, 'memory_current_bytes': 1024, 'memory_max': '4096'}
        first = module.kernel_summary('container', kernel, 10, {})
        self.assertIsNone(first['cpu'])
        second = module.kernel_summary('container', kernel, 12, {'container': (10, 1000000)})
        self.assertEqual(second['cpu'], '200.000%')
        self.assertEqual(second['memory'], '1024 / 4096')

    def test_failed_observation_publishes_independent_failure_and_keeps_samples(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            result = {'state': 'observing', 'started_at': 10, 'host_database_samples': [{'at': 20}]}
            module.finish_observation(output, result, RuntimeError('PRIVATE SQL AND PASSWORD'))
            row = json.loads(output.read_text())
            self.assertEqual(row['state'], 'failed')
            self.assertEqual(row['error_type'], 'RuntimeError')
            self.assertEqual(row['host_database_samples'], [{'at': 20}])
            self.assertFalse(row['formal_capacity_accepted'])
            self.assertNotIn('PRIVATE', output.read_text())
            with self.assertRaises(FileExistsError):
                module.finish_observation(output, result, None)

    def test_blocking_chain_keeps_wait_type_and_relation_but_no_answers(self):
        module = self.module()
        self.assertIn('pg_blocking_pids', module.DATABASE_WAIT_SQL)
        self.assertIn('wait_event', module.DATABASE_WAIT_SQL)
        self.assertIn('xact_start', module.DATABASE_WAIT_SQL)
        self.assertIn('query_fingerprint', module.DATABASE_WAIT_SQL)
        self.assertNotIn('row_to_json(a)', module.DATABASE_WAIT_SQL)
        self.assertNotIn('a.query AS query', module.DATABASE_WAIT_SQL)

    def test_process_disappearance_reinspects_same_container_and_retries_changed_pid_once(self):
        module = self.module()
        item = {'Id': 'a' * 64, 'State': {'Running': True, 'Pid': 100}}
        restarted = deepcopy(item); restarted['State']['Pid'] = 101
        with patch.object(module, 'cgroup_path', side_effect=[FileNotFoundError(), Path('/fresh')]) as paths, \
                patch.object(module, 'cgroup_metrics', return_value={'working_set_bytes': 123}), \
                patch.object(module, 'reinspect_container', return_value=restarted) as inspect:
            value = module.sample_container(item, required=True)
        self.assertEqual(paths.call_count, 2)
        inspect.assert_called_once_with(item['Id'])
        self.assertEqual(value['container']['State']['Pid'], 101)
        self.assertEqual(value['metrics']['working_set_bytes'], 123)
        self.assertEqual(value['lifecycle']['status'], 'restarted_retried')

    def test_required_stop_unchanged_missing_pid_and_second_race_fail_closed(self):
        module = self.module()
        item = {'Id': 'a' * 64, 'State': {'Running': True, 'Pid': 100}}
        stopped = deepcopy(item); stopped['State'] = {'Running': False, 'Pid': 0}
        restarted = deepcopy(item); restarted['State']['Pid'] = 101
        for fresh in (stopped, item, restarted):
            with patch.object(module, 'cgroup_path', side_effect=FileNotFoundError()) as paths, \
                    patch.object(module, 'reinspect_container', return_value=fresh), self.assertRaises(ValueError):
                module.sample_container(item, required=True)
            self.assertLessEqual(paths.call_count, 2)

    def test_stopped_optional_container_has_explicit_skip_without_zero_resources(self):
        module = self.module()
        item = {'Id': 'a' * 64, 'State': {'Running': True, 'Pid': 100}}
        stopped = deepcopy(item); stopped['State'] = {'Running': False, 'Pid': 0}
        with patch.object(module, 'cgroup_path', side_effect=FileNotFoundError()), \
                patch.object(module, 'reinspect_container', return_value=stopped):
            value = module.sample_container(item, required=False)
        self.assertIsNone(value['metrics'])
        self.assertEqual(value['lifecycle']['status'], 'skipped_stopped_optional')
        self.assertNotIn('working_set_bytes', value['lifecycle'])

    def test_oneoff_web_never_substitutes_for_required_main_web(self):
        module = self.module()
        main = {'Config': {'Labels': {'com.docker.compose.project': 'kt-school-e2e',
                                     'com.docker.compose.service': 'web', 'com.docker.compose.oneoff': 'False'}}}
        oneoff = deepcopy(main); oneoff['Config']['Labels']['com.docker.compose.oneoff'] = 'True'
        self.assertTrue(module.required_service(main, 'kt-school-e2e', {'web'}))
        self.assertFalse(module.required_service(oneoff, 'kt-school-e2e', {'web'}))
        self.assertEqual(module.sampled_service_roles([oneoff], 'kt-school-e2e'), set())
        self.assertEqual(module.sampled_service_roles([main, oneoff], 'kt-school-e2e'), {'web'})
