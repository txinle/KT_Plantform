import importlib.util
import tempfile
import json
import subprocess
import io
import unittest
import asyncio
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch


ROOT = Path(__file__).resolve().parents[2]


class SchoolDiagnosticRuntimeTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'scripts/ops/school_diagnostic_runtime.py'
        self.assertTrue(path.exists(), 'operational diagnostic adapter missing')
        spec = importlib.util.spec_from_file_location('school_diagnostic_runtime', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def runtime(self, directory):
        module = self.module()
        return module, module.DiagnosticRuntime({'run_id': 'diagnostic', 'candidate_dir': directory,
            'private_dir': directory, 'source_config': str(Path(directory) / 'source-config.json'),
            'compose_overlay_path': str(Path(directory) / 'images.json'),
            'images': {role: 'sha256:' + 'a' * 64 for role in module.ROLES},
            'anchor_manifest': str(Path(directory) / 'anchor.json'), 'pair_dir': str(Path(directory) / 'pair'),
            'fixture_password_file': str(Path(directory) / 'password'),
            'production_health_url': 'http://127.0.0.1:8080/health/'})

    def test_compose_and_direct_db_commands_are_fixed_and_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            command = runtime.direct_command(Path(directory), ['manage.py', 'verify_school_protocol_load'])
            self.assertEqual(runtime.compose[:5], ['docker', 'compose', '-p', 'kt-school-e2e', '--env-file'])
            self.assertIn('POSTGRES_DB=kt_e2e', command)
            self.assertIn('POSTGRES_USER=kt_e2e_user', command)
            self.assertIn('POSTGRES_HOST=school-test-postgres', command)
            self.assertEqual(sum(arg == '-f' for arg in command), 5)
            self.assertNotIn('migrate', command)
            self.assertNotIn('down', command)

    def test_load_command_has_owned_labels_and_fixed_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            _, runtime = self.runtime(directory)
            command = runtime.load_command(Path(directory), 'fresh-attempt', ['scripts/load/school_short_storm.py'])
            self.assertIn('512m', command)
            self.assertIn('256', command)
            self.assertIn('host', command)
            self.assertIn('com.docker.compose.project=kt-school-e2e', command)
            self.assertIn('com.docker.compose.service=load', command)
            self.assertIn('school.diagnostic.fixture=fresh-attempt', command)
            self.assertIn('sha256:' + 'a' * 64, command)

    def test_three_failed_production_probes_interrupt_but_one_does_not(self):
        module = self.module()
        guard = module.SafetyGuard()
        good = {'memory_available_bytes': 3 * 1024**3, 'postgres_available': True, 'disk_error': False}
        self.assertIsNone(guard.update({**good, 'production_healthy': False}))
        self.assertIsNone(guard.update({**good, 'production_healthy': False}))
        self.assertEqual(guard.update({**good, 'production_healthy': False}), 'production_health_failed_three_times')
        self.assertEqual(module.SafetyGuard().update({**good, 'production_healthy': True, 'postgres_available': False}), 'shared_postgres_unavailable')
        self.assertEqual(module.SafetyGuard().update({**good, 'production_healthy': True, 'memory_available_bytes': 1024**3}), 'host_memory_below_2_gib')

    def test_native_guard_cannot_be_selected_without_bound_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            config = {**runtime.config, 'postgres_guard': 'native'}
            with self.assertRaises(module.SafetyInterrupted):
                module.DiagnosticRuntime(config)

    def test_native_failures_never_pass_the_guard_even_with_successful_docker(self):
        module = self.module()
        for outcome in ('timeout', 'connect_error', 'query_error', 'identity_error', 'os_error', 'heartbeat_lost'):
            guard = module.SafetyGuard()
            sample = {'memory_available_bytes': 3 * 1024**3, 'production_healthy': True,
                      'postgres_available': False, 'postgres_guard': 'native',
                      'native_postgres_probe': {'outcome': outcome}, 'disk_error': False}
            self.assertEqual(guard.update(sample), 'shared_postgres_unavailable')

    def test_safety_sample_records_database_probe_failure_without_weakening_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            for outcome, expected in ((SimpleNamespace(returncode=2), 'returncode'),
                                      (subprocess.TimeoutExpired('docker', 1), 'timeout'),
                                      (OSError('unavailable'), 'os_error')):
                with self.subTest(expected=expected), \
                        patch.object(module.Path, 'read_text', return_value='MemAvailable: 4194304 kB'), \
                        patch.object(module.urllib.request, 'urlopen', return_value=nullcontext(SimpleNamespace(status=200))), \
                        patch.object(module.os, 'statvfs', return_value=SimpleNamespace(f_bavail=1), create=True), \
                        patch.object(module.subprocess, 'run') as probe:
                    if isinstance(outcome, Exception):
                        probe.side_effect = outcome
                    else:
                        probe.return_value = outcome
                    sample = runtime.safety_sample(Path(directory))
                    self.assertFalse(sample['postgres_available'])
                    self.assertEqual(sample['postgres_probe']['outcome'], expected)
                    self.assertGreaterEqual(sample['postgres_probe']['elapsed_seconds'], 0)
                    self.assertEqual(sample['postgres_probe']['returncode'], 2 if expected == 'returncode' else None)
                    self.assertEqual(probe.call_args.kwargs['timeout'], 1)
                    self.assertEqual(runtime.guard.update(sample), 'shared_postgres_unavailable')

    def test_only_owned_test_roles_can_be_stopped(self):
        module = self.module()
        def item(identity, project, role, image='sha256:' + 'a' * 64):
            return {'Id': identity, 'Image': image, 'Config': {'Labels': {
                'com.docker.compose.project': project, 'com.docker.compose.service': role}}}
        images = {role: 'sha256:' + 'a' * 64 for role in module.ROLES}
        items = [item('production', 'kt_plantform', 'web'), item('shared-pg', 'kt_plantform', 'db'),
                 item('test-pg', 'kt-school-e2e', 'db'), item('test-web', 'kt-school-e2e', 'web')]
        self.assertEqual(module.owned_test_ids(items, images), ['test-web'])

    def test_missing_observer_or_partial_history_cannot_pass(self):
        module = self.module()
        result = module.score_stage('baseline-300', {'workflow_failures': 0}, {}, history_matched=False)
        self.assertFalse(result['performance_passed'])
        self.assertFalse(result['formal_capacity_accepted'])
        self.assertFalse(result['checks']['matched_complete_history'])
        self.assertFalse(result['checks']['observer_covers_load'])

    def test_runtime_rejects_wrong_worker_image_and_database_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            items = []
            for role in module.ROLES:
                env = ['POSTGRES_DB=kt_e2e', 'POSTGRES_USER=kt_e2e_user', 'CAPTURE_V2_ENABLED=1', 'CAPTURE_V2_COURSE_IDS=*']
                env += ['POSTGRES_HOST=' + ('school-test-postgres' if role == 'scheduler' else 'pgbouncer'),
                        'POSTGRES_POOL_ALIAS=' + module.POOL_ALIASES.get(role, ''), 'PGBOUNCER_SERVER_HOST=school-test-postgres']
                if role in module.WORKERS:
                    env.append('GUNICORN_WORKERS=' + str(module.WORKERS[role]))
                items.append({'Id': role, 'Image': runtime.config['images'][role], 'State': {'Running': True},
                              'HostConfig': {'Memory': 1, 'NanoCpus': 0, 'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]}},
                              'Config': {'Env': env, 'Labels': {'com.docker.compose.project': module.PROJECT,
                                                               'com.docker.compose.service': role}}})
            runtime.validate_containers(items)
            items.append({'Id': 'native-helper', 'Config': {'Labels': {
                'com.docker.compose.project': module.PROJECT, 'com.docker.compose.service': 'native-db-probe',
                'com.docker.compose.oneoff': 'true'}}})
            runtime.validate_containers(items)
            runtime.config['source_config'] = {'roles': {'web': {'workers': 16}}}
            items[0]['Config']['Env'] = [value.replace('GUNICORN_WORKERS=12', 'GUNICORN_WORKERS=16') for value in items[0]['Config']['Env']]
            runtime.validate_containers(items)
            items[0]['Config']['Env'].append('GUNICORN_WORKERS=12')
            with self.assertRaises(module.SafetyInterrupted):
                runtime.validate_containers(items)
            items[0]['Config']['Env'].pop()
            items[0]['Image'] = 'sha256:' + 'b' * 64
            with self.assertRaises(module.SafetyInterrupted):
                runtime.validate_containers(items)

    def test_failed_observation_is_explicit_failure_even_if_oom_count_zero(self):
        module = self.module()
        result = module.score_stage('baseline-300', {'started_at': 1, 'ended_at': 2},
                                   {'source': 'docker_events', 'state': 'failed', 'oom_kills': 0,
                                    'started_at': 0, 'ended_at': 3, 'host_database_samples': [1]})
        self.assertFalse(result['checks']['observer_completed_without_error'])

    def test_completed_short_capture_and_observation_can_pass(self):
        module = self.module()
        metrics = {key: {'count': 3 * (20 if key in ('enter_item', 'save_draft', 'event_batch', 'submit_item') else 1),
                         'p95_seconds': .1, 'p99_seconds': .2}
                   for key in ('login', 'start', 'resume', 'enter_item', 'save_draft', 'event_batch', 'submit_item', 'finish')}
        protocol = {'contract': module.CONTRACT, 'all_events_acknowledged': True, 'completed_sessions': 3,
                    'scenarios': {kind: 3 for kind in ('single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer',
                        'ime_protocol_simulation', 'queued_replay_reload_protocol', 'writer_lease_reacquire', 'required_check',
                        'hint_observed_protocol', 'authorized_feedback_protocol')}}
        http = {'student_count': 3, 'metrics': metrics, 'errors': [], 'http_error_count': 0,
                'session_count': 3, 'unique_session_count': 3, 'started': 1, 'ended': 2, 'capture_protocol': protocol}
        observation = {'source': 'docker_events', 'state': 'completed', 'started_at': 0, 'ended_at': 3,
                       'oom_kills': 0, 'swap_start_bytes': 0, 'swap_end_bytes': 0, 'host_database_samples': [1]}
        self.assertTrue(module.score_stage('short-3', http, observation)['performance_passed'])

    def test_empty_http_result_is_explicit_invalid_evidence_and_collect_still_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            _, runtime = self.runtime(directory)
            attempt = Path(directory)
            (attempt / 'http.json').write_text('')
            runtime.bind_attempt = Mock()
            runtime.stop_load = Mock()
            runtime.run_private = Mock(return_value=0)
            runtime.backlog = Mock(return_value={'research': 4})
            result = runtime.collect('short-3', attempt, 'unique-fixture')
            self.assertFalse(result['performance_passed'])
            self.assertEqual(result['evidence_errors']['http']['error_type'], 'JSONDecodeError')
            self.assertTrue((attempt / 'backlog-after.json').exists())
            self.assertIsNotNone(runtime.load_name)

    def test_mixed_final_score_requires_measured_teacher_freshness(self):
        module = self.module()
        result = module.score_stage('mixed-300', {}, {})
        self.assertFalse(result['checks']['teacher_initial_data_ready_within_30_seconds'])
        self.assertFalse(result['checks']['teacher_latest_source_ready_within_30_seconds'])

    def test_read_only_json_query_handles_psql_transaction_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            _, runtime = self.runtime(directory)
            runtime.capture = Mock(return_value='BEGIN\n{"research":4}\nCOMMIT')
            self.assertEqual(runtime.db_json('SELECT json_build_object()'), {'research': 4})
            runtime.capture.return_value = '0'
            self.assertEqual(runtime.db_json('SELECT to_json(count(*))'), 0)

    def test_runtime_start_never_builds_or_pulls_or_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            command = runtime.start_command()
            self.assertIn('--no-build', command)
            self.assertEqual(command[command.index('--pull') + 1], 'never')
            self.assertEqual(command[-len(module.ROLES):], list(module.ROLES))
            self.assertNotIn('migrate', command)

    def test_safety_monitor_keeps_two_second_sample_period(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            process = Mock(returncode=0)
            process.poll.side_effect = [None, 0]
            runtime.safety_sample = Mock(return_value={'memory_available_bytes': 3 * 1024**3,
                'production_healthy': True, 'postgres_available': True, 'disk_error': False})
            runtime.stop_load = Mock()
            runtime.terminate_child = Mock()
            with patch.object(module.subprocess, 'Popen', return_value=process), \
                 patch.object(module.time, 'monotonic', side_effect=[0, 0, .2, .4]), patch.object(module.time, 'sleep') as sleep:
                runtime.monitored_process(['docker', '--name', 'owned'], Path(directory), 'load', 30)
            self.assertAlmostEqual(sleep.call_args.args[0], 1.6)

    def test_unavailable_direct_postgres_is_safety_interruption_before_seeding(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            runtime.inspect_test = Mock(return_value=[])
            runtime.validate_containers = Mock()
            runtime.capture = Mock(return_value=json.dumps([{'State': {'Running': True},
                'Config': {'Labels': {'com.docker.compose.project': 'kt_plantform'}},
                'NetworkSettings': {'Networks': {module.PROJECT + '_default': {'Aliases': ['school-test-postgres']}}}}]))
            runtime.db_json = Mock(side_effect=subprocess.CalledProcessError(1, ['docker']))
            with self.assertRaises(module.SafetyInterrupted):
                runtime.verify_runtime()

    def test_direct_operation_timeout_stops_only_its_named_oneoff(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            command = runtime.direct_command(Path(directory), ['manage.py', 'seed_school_mixed_load'])
            process = Mock()
            process.wait.side_effect = subprocess.TimeoutExpired(command, 5)
            runtime.terminate_child = Mock()
            runtime.stop_oneoff = Mock()
            with patch.object(module.subprocess, 'Popen', return_value=process):
                self.assertEqual(runtime.run_private(command, Path(directory), 'seed', 5), 124)
            runtime.stop_oneoff.assert_called_once_with(command[command.index('--name') + 1])

    def test_matched_pair_seeding_has_1800_second_pre_matrix_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            _, runtime = self.runtime(directory)
            runtime.verify_runtime = Mock()
            runtime.bind_attempt = Mock()
            runtime.run_private = Mock(side_effect=RuntimeError('test stops before seeding'))
            with self.assertRaisesRegex(RuntimeError, 'test stops'):
                runtime.prepare(Path(directory) / 'pair')
            self.assertEqual(runtime.run_private.call_args.args[-1], 1800)

    def test_complete_seed_can_resume_only_with_matching_binding_and_cold_database(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            pair = Path(directory) / 'pair'
            pair.mkdir()
            (pair / 'raw').mkdir()
            run = 'dg-test'
            for suffix in ('baseline', 'mixed', 'baseline-history', 'mixed-history'):
                phase = suffix.split('-')[0]
                fixture = {'database': 'kt_e2e', 'capture_protocol': module.CONTRACT, 'student_count': 300,
                           'item_count': 20, 'task_id': len(suffix), 'experiment_id': len(suffix) + 10,
                           'course_id': 1 if phase == 'baseline' else 2, 'class_id': 1 if phase == 'baseline' else 2,
                           'usernames': [phase + str(i) for i in range(300)], 'teacher_usernames': [phase + 't' + str(i) for i in range(5)],
                           'run_id': run + '-' + phase, 'password': 'private'}
                (pair / 'raw' / (run + '-' + suffix + '.json')).write_text(json.dumps(fixture))
            runtime.inspect_test = Mock(return_value=[])
            runtime.db_json = Mock(return_value={'mapping': 1, 'participants': 300, 'items': 20, 'sessions': 0, 'roster': 300, 'teachers': 5})
            runtime.validate_seeded_pair(pair, run)
            runtime.db_json.return_value['sessions'] = 1
            with self.assertRaises(module.SafetyInterrupted):
                runtime.validate_seeded_pair(pair, run)

    def test_oneoff_cleanup_refuses_production_or_foreign_container(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            item = {'Id': 'production', 'Image': runtime.config['images']['web'], 'Config': {'Labels': {
                'com.docker.compose.project': 'kt_plantform', 'com.docker.compose.service': 'web',
                'com.docker.compose.oneoff': 'True', 'school.diagnostic.operation': 'owned'}}}
            with patch.object(module.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps([item]))) as run:
                with self.assertRaises(module.SafetyInterrupted):
                    runtime.stop_oneoff('owned')
            self.assertEqual(run.call_count, 1)

    def test_prepare_resumes_completed_seed_without_reseeding_or_overwriting_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            pair = Path(directory) / 'pair'
            pair.mkdir(); (pair / 'raw').mkdir()
            (pair / 'prepare-started.json').write_text(json.dumps({'runtime_sha256': module.fingerprint(runtime.config)}))
            run = 'dg-' + module.hashlib.sha256((runtime.config['run_id'] + str(pair)).encode()).hexdigest()[:16]
            original = {}
            for suffix in ('baseline', 'mixed', 'baseline-history', 'mixed-history'):
                path = pair / 'raw' / f'{run}-{suffix}.json'
                path.write_text(json.dumps({'task_id': len(suffix), 'password': 'private'}))
                original[path] = path.read_bytes()
            runtime.verify_runtime = Mock()
            runtime.bind_attempt = Mock()
            runtime.validate_seeded_pair = Mock()
            runtime.source_value = Mock(return_value={'source_sha256': 'a' * 64})
            runtime.run_private = Mock()
            runtime.monitored_process = Mock(side_effect=RuntimeError('history would start'))
            with self.assertRaisesRegex(RuntimeError, 'history would start'):
                runtime.prepare(pair)
            runtime.run_private.assert_not_called()
            runtime.validate_seeded_pair.assert_called_once_with(pair, run)
            runtime.bind_attempt.assert_called_once_with(pair, create=False)
            self.assertTrue(all(path.read_bytes() == body for path, body in original.items()))

    def test_history_preparation_has_6000_second_bound_and_25_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            pair = Path(directory) / 'pair'
            pair.mkdir(); (pair / 'raw').mkdir()
            module.write_once(pair / 'prepare-started.json', {'runtime_sha256': module.fingerprint(runtime.config)})
            run = 'dg-' + module.hashlib.sha256((runtime.config['run_id'] + str(pair)).encode()).hexdigest()[:16]
            for suffix in ('baseline', 'mixed', 'baseline-history', 'mixed-history'):
                module.write_once(pair / 'raw' / f'{run}-{suffix}.json', {'task_id': len(suffix), 'password': 'private'})
            runtime.verify_runtime = Mock(); runtime.bind_attempt = Mock(); runtime.validate_seeded_pair = Mock()
            runtime.source_value = Mock(return_value={'source_sha256': 'a' * 64})
            runtime.backlog = Mock(return_value={})
            def load(command, attempt, phase, *, timeout):
                module.write_once(attempt / (phase + '-http.json'), {'historical_workflow_complete': True,
                    'capture_protocol': {'completed_sessions': 300}})
                return 0
            def verify(command, attempt, label, timeout):
                module.write_once(attempt / (label.removesuffix('-verify') + '-persistence.json'), {'passed': True})
                return 0
            runtime.monitored_process = Mock(side_effect=load)
            runtime.run_private = Mock(side_effect=verify)
            report = runtime.prepare(pair)
            self.assertEqual(runtime.monitored_process.call_count, 2)
            self.assertTrue(all(call.kwargs['timeout'] == 6000 for call in runtime.monitored_process.call_args_list))
            self.assertEqual(report['history_preparation_concurrency'], 25)
            self.assertEqual(report['history_preparation_timeout_seconds'], 6000)
            self.assertTrue(report['matched_complete_history'])
            self.assertIn('operation,25,synchronized=False', module.HISTORY_SCRIPT)
            self.assertIn("'concurrent_http_students':25", module.HISTORY_SCRIPT)
            self.assertEqual(module.read(pair / 'prepared.json'), report)

    def test_long_script_exports_recorded_arrivals_and_start_delay_without_losing_client_resources(self):
        module = self.module()
        output = io.StringIO()
        arrivals = [{'item_index': 1, 'send_delay_seconds': 2.5}]
        files = {'result': output}
        def phase(args, manifest, evidence):
            evidence['runner'] = SimpleNamespace(item_arrivals=arrivals, send_delays=[.1, .3])
            return {'workflow_failures': 0, 'client_cpu_seconds': 1.25, 'client_peak_rss_bytes': 8192}
        mocked = {'school_mixed_300': SimpleNamespace(phase=phase),
                  'capture_protocol': SimpleNamespace(evidence_files=lambda *args: nullcontext(files))}
        with patch.dict(module.sys.modules, mocked), patch('builtins.open', mock_open(read_data='{}')):
            with self.assertRaises(SystemExit) as completed:
                exec(module.LONG_SCRIPT, {'PHASE': 'mixed'})
        self.assertEqual(completed.exception.code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['item_arrivals'], arrivals)
        self.assertEqual(result['generator']['start_send_delay']['count'], 2)
        self.assertEqual(result['generator']['start_send_delay']['max_seconds'], .3)
        self.assertIn('previous service waits', result['generator']['item_arrival_delay_interpretation'])
        self.assertEqual(result['client_cpu_seconds'], 1.25)
        self.assertEqual(result['client_peak_rss_bytes'], 8192)


class NativeSchoolProbeTests(unittest.IsolatedAsyncioTestCase):
    def module(self):
        path = ROOT / 'scripts/ops/school_native_db_probe.py'
        self.assertTrue(path.exists(), 'long-lived native probe missing')
        spec = importlib.util.spec_from_file_location('school_native_db_probe_test', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def config(self):
        return {'host': '127.0.0.1', 'port': 5432, 'dbname': 'kt_e2e', 'user': 'kt_e2e_user',
                'password': 'PRIVATE PASSWORD', 'expected_server_address': '127.0.0.1', 'expected_server_port': 5432}

    def fake_connection(self, *, identity=None, query_error=None):
        from unittest.mock import AsyncMock
        cursor = AsyncMock()
        cursor.__aenter__.return_value = cursor
        cursor.fetchone.return_value = identity or ('kt_e2e', 'kt_e2e_user', 'kt_e2e_user', '127.0.0.1', 5432, 1)
        cursor.execute.side_effect = query_error
        connection = Mock()
        connection.cursor.return_value = cursor
        return connection, cursor

    async def test_each_sample_authenticates_fresh_connection_and_identity_query(self):
        module = self.module()
        from unittest.mock import AsyncMock
        connection, cursor = self.fake_connection()
        connect = AsyncMock(return_value=connection)
        with patch('psycopg.AsyncConnection.connect', connect):
            first = await module.probe(self.config(), 'a' * 32)
            second = await module.probe(self.config(), 'b' * 32)
        self.assertEqual(connect.await_count, 2)
        self.assertEqual(first['outcome'], 'ready')
        self.assertEqual(second['request_id'], 'b' * 32)
        self.assertEqual(cursor.execute.await_count, 2)
        self.assertGreaterEqual(first['connect_seconds'], 0)
        self.assertGreaterEqual(first['query_seconds'], 0)
        self.assertLess(first['total_seconds'], 1)
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.assertEqual(connection.pgconn.finish.call_count, 2)

    async def test_identity_sql_canonicalizes_inet_address_and_preserves_exact_binding(self):
        module = self.module()
        self.assertIn('host(inet_server_addr())', module.IDENTITY_SQL)
        self.assertNotIn('inet_server_addr()::text', module.IDENTITY_SQL)
        from unittest.mock import AsyncMock
        connection, _ = self.fake_connection(identity=(
            'kt_e2e', 'kt_e2e_user', 'kt_e2e_user', '127.0.0.1/32', 5432, 1))
        with patch('psycopg.AsyncConnection.connect', AsyncMock(return_value=connection)):
            row = await module.probe(self.config(), 'a' * 32)
        self.assertEqual(row['outcome'], 'identity_error')
        self.assertNotIn('identity_sha256', row)

    async def test_connect_query_identity_and_os_failures_are_explicit_and_sanitized(self):
        module = self.module()
        from unittest.mock import AsyncMock
        for stage, expected in [('connect', 'connect_error'), ('query', 'query_error'),
                                ('identity', 'identity_error'), ('os', 'os_error')]:
            connection, _ = self.fake_connection(identity=('wrong',) if stage == 'identity' else None,
                query_error=RuntimeError('PRIVATE SQL') if stage == 'query' else None)
            error = OSError('PRIVATE ADDRESS') if stage == 'os' else RuntimeError('PRIVATE PASSWORD')
            connect = AsyncMock(side_effect=error if stage in ('connect', 'os') else None, return_value=connection)
            with patch('psycopg.AsyncConnection.connect', connect):
                row = await module.probe(self.config(), 'a' * 32)
            self.assertEqual(row['outcome'], expected)
            self.assertNotIn('PRIVATE', json.dumps(row))

    async def test_connect_and_query_share_one_total_deadline(self):
        module = self.module()
        connection, cursor = self.fake_connection()
        async def connect(**kwargs):
            await asyncio.sleep(.05)
            return connection
        async def query(*args):
            await asyncio.sleep(.1)
        cursor.execute.side_effect = query
        with patch('psycopg.AsyncConnection.connect', side_effect=connect):
            row = await module.probe(self.config(), 'a' * 32, timeout=.12)
        self.assertEqual(row['outcome'], 'timeout')
        self.assertEqual(row['timeout_phase'], 'query')
        self.assertLess(row['total_seconds'], .3)
        connection.pgconn.finish.assert_called_once()

    async def test_driver_cancellation_cleanup_cannot_extend_the_total_deadline(self):
        module = self.module()
        from unittest.mock import AsyncMock
        connection, cursor = self.fake_connection()
        async def slow_query(*args):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(.3)
                raise
        cursor.execute.side_effect = slow_query
        with patch('psycopg.AsyncConnection.connect', AsyncMock(return_value=connection)):
            row = await module.probe(self.config(), 'a' * 32, timeout=.05)
        self.assertEqual(row['outcome'], 'timeout')
        self.assertLess(row['total_seconds'], .15)
        connection.pgconn.finish.assert_called_once()

    async def test_wrong_database_config_is_rejected_before_connecting(self):
        module = self.module()
        with patch('psycopg.AsyncConnection.connect') as connect:
            row = await module.probe({**self.config(), 'dbname': 'kt_platform'}, 'a' * 32)
        self.assertEqual(row['outcome'], 'identity_error')
        connect.assert_not_called()

    async def test_missing_reply_and_stale_reply_are_lost_heartbeat(self):
        module = self.module()
        client = module.NativeProbeClient({})
        ticket = {'request_id': 'a' * 32, 'deadline': 0}
        self.assertEqual(client.finish(ticket)['outcome'], 'heartbeat_lost')
        client.replies.put({'request_id': 'b' * 32, 'outcome': 'ready'})
        self.assertEqual(client.finish(ticket)['outcome'], 'heartbeat_lost')

    async def test_docker_helper_is_one_long_lived_owned_process_with_read_only_secret_mount(self):
        module = self.module()
        client = module.NativeProbeClient({'secret_file': '/root/private/probe.json',
                                          'image': 'sha256:' + 'a' * 64})
        self.assertTrue(hasattr(client, 'command'), 'owned Docker helper launch missing')
        command = client.command()
        self.assertEqual(command[:2], ['docker', 'run'])
        self.assertIn('-i', command)
        self.assertIn('128m', command)
        self.assertIn('/root/private/probe.json:/probe-secret.json:ro', command)
        self.assertNotIn('exec', command)
        self.assertIn('com.docker.compose.service=native-db-probe', command)
        self.assertIn('com.docker.compose.oneoff=true', command)
        self.assertEqual(command[-2:], ['--config', '/probe-secret.json'])

    async def test_coordinator_import_does_not_require_a_host_psycopg_install(self):
        script = """import builtins,runpy,sys
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith('psycopg'): raise ImportError('Host driver deliberately unavailable')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
runpy.run_path(sys.argv[1],run_name='import_only')
"""
        result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'scripts/ops/school_diagnostic_runtime.py')],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_observer_missing_and_stale_probe_evidence_are_explicit(self):
        sys.path.insert(0, str(ROOT / 'scripts/load'))
        import observe_school_runtime
        self.assertTrue(hasattr(observe_school_runtime, 'probe_progress'), 'paired probe observation missing')
        probe_progress = observe_school_runtime.probe_progress
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'probe.json'
            self.assertEqual(probe_progress(path, 10)['state'], 'missing')
            path.write_text(json.dumps({'at': 1, 'postgres_guard': 'docker', 'native_postgres_probe': {'outcome': 'ready'}}))
            self.assertEqual(probe_progress(path, 20)['state'], 'stale')
            fresh = probe_progress(path, 2)
            self.assertEqual(fresh['state'], 'sampled')
            self.assertEqual(fresh['age_seconds'], 1)
            self.assertEqual(fresh['native_postgres_probe']['outcome'], 'ready')

    async def test_helper_cleanup_refuses_a_foreign_container(self):
        module = self.module()
        client = module.NativeProbeClient({'secret_file': '/root/private/probe.json',
                                          'image': 'sha256:' + 'a' * 64})
        self.assertTrue(hasattr(client, 'command'), 'owned Docker helper cleanup missing')
        client.command()
        client.process = Mock(stdin=Mock(), stdout=Mock())
        client.process.poll.return_value = 0
        foreign = {'Id': 'shared-postgres', 'Image': 'sha256:' + 'a' * 64,
                   'Config': {'Labels': {'com.docker.compose.project': 'kt_plantform'}}}
        with patch.object(module.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps([foreign]))) as run:
            with self.assertRaisesRegex(ValueError, 'unowned'):
                client.close()
        self.assertEqual(run.call_count, 1)

    async def test_calibration_requires_two_distinct_overlapping_pairs_and_bound_fault_evidence(self):
        module = self.module()
        binding = {'source': 'same', 'probe_secret_sha256': 'a' * 64, 'native_identity_sha256': 'b' * 64,
                   'helper_sha256': module.helper_hash()}
        def pair(identity, *, start=10):
            return {'postgres_probe': {'source': 'docker_exec_pg_isready', 'outcome': 'timeout',
                        'started_monotonic': 10, 'ended_monotonic': 11},
                    'native_postgres_probe': {'source': 'native_authenticated_sql', 'outcome': 'ready',
                        'request_id': identity * 32, 'deadline_seconds': 1, 'total_seconds': .1, 'ipc_seconds': .2,
                        'probe_secret_sha256': binding['probe_secret_sha256'],
                        'identity_sha256': binding['native_identity_sha256'], 'helper_sha256': module.helper_hash(),
                        'started_monotonic': start, 'ended_monotonic': start + .1}}
        certificate = {'binding': binding, 'samples': [pair('a')],
                       'fault_tests': {'passed': True, 'helper_sha256': module.helper_hash(),
                                       'cases': sorted(module.FAULT_CASES)}}
        self.assertFalse(module.validate_calibration(certificate, binding))
        certificate['samples'].append(pair('a'))
        self.assertFalse(module.validate_calibration(certificate, binding))
        certificate['samples'][1] = pair('b', start=11)
        self.assertFalse(module.validate_calibration(certificate, binding))
        certificate['samples'][1] = pair('b')
        self.assertTrue(module.validate_calibration(certificate, binding))
        certificate['samples'][1]['native_postgres_probe']['identity_sha256'] = 'c' * 64
        self.assertFalse(module.validate_calibration(certificate, binding))
        certificate['samples'][1] = pair('b')
        self.assertFalse(module.validate_calibration(certificate, {**binding, 'source': 'changed'}))
        certificate['fault_tests']['cases'].remove('timeout')
        self.assertFalse(module.validate_calibration(certificate, binding))



class GatewayRoutingTests(unittest.TestCase):
    def runtime(self, directory):
        ops = ROOT / 'scripts/ops'
        sys.path.insert(0, str(ops))
        spec = importlib.util.spec_from_file_location('school_gateway_test', ops / 'school_architecture_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runtime = module.ArchitectureRuntime({'run_id': 'gateway-test', 'candidate_dir': directory,
            'private_dir': directory, 'source_config': {}, 'compose_overlay_path': 'overlay.json',
            'images': {role: 'sha256:' + 'a' * 64 for role in module.ROLES}})
        self.assertTrue(hasattr(runtime, 'verify_gateway_routing'), 'gateway role witness missing')
        return module, runtime

    def fixtures(self, module, runtime, *, misroute=False, missing=False, foreign=False):
        seen = {}
        containers = [{'Id': role + '-id', 'Image': runtime.config['images'][role], 'State': {'Running': True},
                       'Config': {'Env': ['POSTGRES_PASSWORD=PRIVATE PASSWORD'], 'Labels': {
                           'com.docker.compose.project': 'production' if foreign else module.PROJECT,
                           'com.docker.compose.service': role}}}
                      for role in ('web', 'student-read', 'teacher-read')]
        def open_request(request, timeout):
            self.assertLessEqual(timeout, 3)
            identity = request.get_header('X-request-id')
            self.assertRegex(identity, '^[a-f0-9]{32}$')
            role = next(role for role, path, _ in module.GATEWAY_ROUTES if request.full_url.endswith(path))
            seen[identity] = 'student-read' if misroute else role
            if role != 'web':
                raise module.urllib.error.HTTPError(request.full_url, 403, 'PRIVATE DETAIL', {}, None)
            return nullcontext(SimpleNamespace(status=200))
        def docker(command, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 3)
            self.assertEqual(command[0], 'docker')
            if command[1] == 'ps':
                return SimpleNamespace(returncode=0, stdout='web-id\nstudent-read-id\nteacher-read-id', stderr='')
            if command[1] == 'inspect':
                return SimpleNamespace(returncode=0, stdout=json.dumps(containers), stderr='')
            self.assertEqual(command[1], 'logs')
            role = command[-1].removesuffix('-id')
            rows = [] if missing else [{'phase': 'gunicorn_request_complete', 'request_id': identity,
                'PRIVATE': 'PRIVATE ANSWER'} for identity, observed in seen.items() if observed == role]
            return SimpleNamespace(returncode=0, stdout='', stderr='\n'.join('[WARNING] ' + json.dumps(row) for row in rows))
        return seen, open_request, docker

    def test_three_anonymous_requests_are_witnessed_in_the_correct_owned_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            seen, open_request, docker = self.fixtures(module, runtime)
            with patch.object(module.urllib.request, 'build_opener', return_value=SimpleNamespace(open=Mock(side_effect=open_request))) as opener, \
                    patch.object(module.subprocess, 'run', side_effect=docker):
                result = runtime.verify_gateway_routing(Path(directory))
            self.assertTrue(result['passed'])
            self.assertEqual(len(seen), 3)
            self.assertEqual(opener.return_value.open.call_count, 3)
            self.assertEqual(len(set(seen)), 3)
            for row in result['requests']:
                self.assertEqual(row['observed_roles'], [row['expected_role']])
            public = (Path(directory) / 'gateway-routing.json').read_text()
            self.assertNotIn('PRIVATE', public)
            self.assertNotIn('/api/', public)
            self.assertNotIn('password', public.lower())

    def test_misroute_missing_witness_or_foreign_roles_stop_before_any_seed_write(self):
        for failure in ('misroute', 'missing', 'foreign'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                module, runtime = self.runtime(directory)
                _, open_request, docker = self.fixtures(module, runtime, **{failure: True})
                runtime.verify_runtime = Mock()
                runtime.bind_attempt = Mock()
                runtime.run_private = Mock()
                with patch.object(module.urllib.request, 'build_opener', return_value=SimpleNamespace(open=Mock(side_effect=open_request))), \
                        patch.object(module.subprocess, 'run', side_effect=docker):
                    with self.assertRaises(module.SafetyInterrupted):
                        runtime.seed('diagnostic-3', Path(directory))
                runtime.run_private.assert_not_called()
                self.assertFalse(json.loads((Path(directory) / 'gateway-routing.json').read_text())['passed'])

    def test_log_timeout_is_sanitized_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            _, open_request, docker = self.fixtures(module, runtime)
            def timed_out(command, **kwargs):
                if command[1] == 'logs':
                    raise subprocess.TimeoutExpired(['docker', 'PRIVATE COMMAND'], kwargs['timeout'])
                return docker(command, **kwargs)
            with patch.object(module.urllib.request, 'build_opener', return_value=SimpleNamespace(open=Mock(side_effect=open_request))), \
                    patch.object(module.subprocess, 'run', side_effect=timed_out):
                with self.assertRaises(module.SafetyInterrupted):
                    runtime.verify_gateway_routing(Path(directory))
            self.assertNotIn('PRIVATE', (Path(directory) / 'gateway-routing.json').read_text())

    def test_unexpected_http_status_cannot_pass_even_when_roles_match(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            seen, open_request, docker = self.fixtures(module, runtime)
            def invalid_status(request, timeout):
                response = open_request(request, timeout)
                return nullcontext(SimpleNamespace(status=500))
            with patch.object(module.urllib.request, 'build_opener', return_value=SimpleNamespace(open=Mock(side_effect=invalid_status))) as opener, \
                    patch.object(module.subprocess, 'run', side_effect=docker):
                with self.assertRaises(module.SafetyInterrupted):
                    runtime.verify_gateway_routing(Path(directory))
            self.assertEqual(opener.return_value.open.call_count, 3)
            self.assertEqual(len(seen), 3)

    def test_one_total_budget_bounds_http_and_docker_including_log_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            _, open_request, docker = self.fixtures(module, runtime)
            clock = [0]
            def delayed_request(request, timeout):
                try:
                    return open_request(request, timeout)
                finally:
                    clock[0] += timeout
            def delayed_docker(command, **kwargs):
                try:
                    return docker(command, **kwargs)
                finally:
                    clock[0] += kwargs['timeout']
            with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(module.urllib.request, 'build_opener', return_value=SimpleNamespace(open=Mock(side_effect=delayed_request))), \
                    patch.object(module.subprocess, 'run', side_effect=delayed_docker):
                with self.assertRaises(module.SafetyInterrupted):
                    runtime.verify_gateway_routing(Path(directory))
            self.assertLessEqual(clock[0], 18)
            self.assertFalse(json.loads((Path(directory) / 'gateway-routing.json').read_text())['passed'])

    def test_run_stage_requires_the_seed_gateway_witness_before_observer_or_load(self):
        with tempfile.TemporaryDirectory() as directory:
            module, runtime = self.runtime(directory)
            runtime.verify_runtime = Mock()
            runtime.bind_attempt = Mock()
            with patch.object(module.subprocess, 'Popen') as process:
                with self.assertRaises(module.SafetyInterrupted):
                    runtime.run_stage('diagnostic-3', Path(directory), {'mode': 'diagnostic'})
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
