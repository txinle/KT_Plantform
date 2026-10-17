"""Bounded planned restarts must preserve the warmed HTTP processes and identity."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent))
import verify_student_connection_recovery as recovery


class ConnectionRecoveryTests(unittest.TestCase):
    def config(self):
        return {'name': recovery.PROJECT, 'services': {
            'db': {'environment': {'POSTGRES_DB': 'kt_e2e'}},
            'entry': {'environment': {'POSTGRES_DB': 'kt_e2e', 'SCHOOL_POOL_PROFILE': 'shared-entry-v2',
                'DATABASE_CONN_MAX_AGE': '60', 'DATABASE_DISABLE_SERVER_SIDE_CURSORS': '1',
                'GUNICORN_WORKERS': '15', 'DATABASE_APPLICATION_NAME': 'kt-school-entry'}},
            'nginx': {'ports': [{'host_ip': '127.0.0.1', 'published': str(recovery.PORT), 'target': 80}]}}}

    def test_only_owned_reuse60_profile_and_evidence_paths_are_accepted(self):
        path = recovery.ROOT / 'private/compose-matrix-test-REUSE60.json'
        recovery.validate_target(self.config(), path, recovery.ROOT / 'evidence/fixture.json', recovery.ROOT / 'evidence/recovery.json')
        for mode in ('production', 'reuse0', 'external_output'):
            config = self.config()
            output = recovery.ROOT / 'evidence/recovery.json'
            if mode == 'production':
                config['name'] = 'production'
            elif mode == 'reuse0':
                config['services']['entry']['environment']['DATABASE_CONN_MAX_AGE'] = '0'
            else:
                output = Path('/tmp/recovery.json')
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                recovery.validate_target(config, path, recovery.ROOT / 'evidence/fixture.json', output)

    def test_expected_target_restart_cannot_hide_http_worker_replacement(self):
        before = {'http': {'entry': {'container': 'e', 'workers': {'11': '100', '12': '101'}}},
            'services': {'db': {'container': 'd', 'restarts': 0}, 'pgbouncer': {'container': 'p', 'restarts': 0}}}
        after = copy.deepcopy(before)
        after['services']['pgbouncer']['restarts'] = 1
        recovery.validate_snapshot(before, after, 'pgbouncer')
        before['services']['session-submission-worker:s'] = {'container': 's', 'restarts': 0}
        after['services']['session-submission-worker:s'] = {'container': 's', 'restarts': 1}
        recovery.validate_snapshot(before, after, 'pgbouncer')
        after['http']['entry']['workers']['12'] = '999'
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            recovery.validate_snapshot(before, after, 'pgbouncer')

    def test_database_probe_keeps_durability_and_64_connection_limit(self):
        probe = {'database': 'kt_e2e', 'user_exists': True, 'max_connections': 64,
            'fsync': 'on', 'synchronous_commit': 'on', 'full_page_writes': 'on'}
        recovery.validate_database(probe)
        for field, value in (('database', 'kt_platform'), ('max_connections', 128), ('fsync', 'off'), ('user_exists', False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                recovery.validate_database(probe | {field: value})

    def test_sequence_warms_before_each_separate_restart_and_reuses_authentication(self):
        probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
        probe.report = {'stages': []}
        probe.preflight = Mock()
        probe.login = Mock()
        probe.snapshot = Mock(return_value={'http': {}, 'services': {}})
        probe.exercise = Mock(return_value={'authenticated': True, 'fresh_database_read': True})
        probe.restart = Mock()
        probe.wait_ready = Mock()
        probe.assert_idle = Mock()
        probe.persist = Mock()
        probe.close = Mock()
        order = Mock()
        for name in ('exercise', 'restart', 'wait_ready', 'assert_idle'):
            order.attach_mock(getattr(probe, name), name)
        probe.run()
        self.assertEqual(probe.login.call_count, 1)
        self.assertEqual([call.args[0] for call in probe.restart.call_args_list], ['pgbouncer', 'db'])
        self.assertEqual(probe.exercise.call_count, 3)
        self.assertTrue(probe.report['passed'])
        events = [call[0] for call in order.mock_calls]
        self.assertLess(events.index('exercise'), events.index('restart'))
        self.assertTrue(all(events[index - 1] == 'assert_idle' for index, event in enumerate(events) if event == 'restart'))

    def test_load_or_first_read_failure_stops_without_claiming_recovery(self):
        probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
        probe.report = {'stages': []}
        probe.preflight = Mock(side_effect=ValueError('running_load'))
        probe.persist = Mock()
        probe.close = Mock()
        probe.restart = Mock()
        with self.assertRaises(ValueError):
            probe.run()
        self.assertFalse(probe.report['passed'])
        probe.restart.assert_not_called()
        probe.persist.assert_called_once()

    def test_actual_idle_guard_rejects_any_owned_running_pressure_driver(self):
        probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
        probe.containers = Mock(return_value=[{'Config': {'Labels': {'com.docker.compose.service': 'load'}}}])
        with self.assertRaisesRegex(ValueError, 'Running pressure'):
            probe.assert_idle()

    def test_real_http_client_cleanup_persists_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
            probe.output_path = Path(directory) / 'recovery.json'
            probe.client = recovery.HttpClient(f'http://127.0.0.1:{recovery.PORT}')
            probe.report = {'stages': []}
            probe.preflight = Mock(side_effect=ValueError('running_load'))
            with self.assertRaisesRegex(ValueError, 'running_load'):
                probe.run()
            report = json.loads(probe.output_path.read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['failure_reason'], 'running_load')

    def test_report_persists_even_if_client_cleanup_itself_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
            probe.output_path = Path(directory) / 'recovery.json'
            probe.client = recovery.HttpClient(f'http://127.0.0.1:{recovery.PORT}')
            probe.client.close = Mock(side_effect=RuntimeError('close_failure'))
            probe.report = {'stages': []}
            probe.preflight = Mock(side_effect=ValueError('running_load'))
            with self.assertRaises(RuntimeError):
                probe.run()
            report = json.loads(probe.output_path.read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['cleanup_error_type'], 'RuntimeError')

    def test_worker_replacement_during_authenticated_probe_is_detected(self):
        probe = recovery.ConnectionRecovery.__new__(recovery.ConnectionRecovery)
        probe.report = {'stages': []}
        probe.preflight = Mock()
        probe.login = Mock()
        before = {'http': {'entry': {'container': 'e', 'workers': {'11': '100'}}}, 'services': {}}
        changed = copy.deepcopy(before)
        changed['http']['entry']['workers']['11'] = '200'
        probe.snapshot = Mock(side_effect=[before, before, changed])
        probe.exercise = Mock(return_value={'authenticated': True, 'fresh_database_read': True})
        probe.restart = Mock()
        probe.wait_ready = Mock()
        probe.assert_idle = Mock()
        probe.persist = Mock()
        probe.close = Mock()
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            probe.run()
        self.assertFalse(probe.report['passed'])
        self.assertEqual(probe.restart.call_count, 1)


if __name__ == '__main__':
    unittest.main()
