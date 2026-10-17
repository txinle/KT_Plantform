import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import collect_student_capacity_logs as logs


class CapacityLogTests(unittest.TestCase):
    def test_rebound_backend_configuration_uses_light_diagnostics(self):
        import capacity_retest
        import prepare_student_capacity_remote as prepare
        config = {'services': {'web': {'environment': {'SCHOOL_CAPTURE_DIAGNOSTICS_MODE': 'sql'}}}}
        result = prepare.rebind_retest_compose(config, capacity_retest.default_manifest(), 'backend', 'frontend')
        self.assertEqual(result['services']['web']['environment']['SCHOOL_CAPTURE_DIAGNOSTICS_MODE'], 'light')
        self.assertEqual(result['services']['web']['environment']['ASSESSMENT_REQUEST_TIMING_ENABLED'], '1')

    def test_only_owned_retest_can_follow_until_coordinator_stop(self):
        with mock.patch.object(logs, 'PROJECT', 'kt-school-e2e-capacity-20261016'):
            with self.assertRaises(ValueError):
                logs.duration_bound(0)
        with mock.patch.object(logs, 'PROJECT', 'kt-school-e2e-capacity-retest-20261016'):
            self.assertIsNone(logs.duration_bound(0))
            self.assertEqual(logs.duration_bound(43200), 43200)
            with self.assertRaises(ValueError):
                logs.duration_bound(-1)

    def test_coordinator_bound_collection_remains_active_past_twelve_hours(self):
        collector = object.__new__(logs.Collector)
        collector.duration = None
        collector.stop = mock.Mock(exists=mock.Mock(return_value=False))
        collector.live, collector.seen, collector.report = {}, set(), {}
        collector.discover = mock.Mock(return_value=[])
        collector.persist = mock.Mock()
        with mock.patch.object(logs.time, 'monotonic', return_value=50000), \
                mock.patch.object(logs.time, 'sleep', side_effect=lambda _: collector.stop.exists.configure_mock(return_value=True)):
            collector.run()
        collector.discover.assert_called_once()
        self.assertEqual(collector.report['stop_reason'], 'coordinator_marker')
        self.assertEqual(collector.report['updated_at_monotonic'], 50000)

    def item(self):
        return {'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64, 'RestartCount': 0,
            'State': {'Running': True, 'StartedAt': '2026-10-16T00:00:00Z'},
            'Config': {'Labels': {'com.docker.compose.project': logs.PROJECT, 'com.docker.compose.service': 'entry'},
                'Env': ['SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=1', 'SCHOOL_CAPTURE_DIAGNOSTICS_MODE=light',
                    'ASSESSMENT_REQUEST_TIMING_ENABLED=1', 'POSTGRES_PASSWORD=private', 'OTHER_SECRET=private']}}

    def test_ownership_filter_and_sanitized_identity_do_not_copy_environment(self):
        item = self.item()
        row = logs.identity(item)
        self.assertEqual(row['service'], 'entry')
        self.assertEqual(row['diagnostics']['SCHOOL_CAPTURE_DIAGNOSTICS_MODE'], 'light')
        self.assertNotIn('private', str(row))
        for role, project in (('entry', 'production'), ('load', logs.PROJECT), ('db', logs.PROJECT)):
            candidate = copy.deepcopy(item)
            candidate['Config']['Labels'].update({'com.docker.compose.service': role, 'com.docker.compose.project': project})
            self.assertIsNone(logs.identity(candidate))

    def test_replacement_and_restart_have_explicit_stream_identities(self):
        before = self.item()
        restarted = copy.deepcopy(before)
        restarted['State']['StartedAt'] = '2026-10-16T00:01:00Z'
        replaced = copy.deepcopy(before)
        replaced['Id'] = 'c' * 64
        self.assertNotEqual(logs.stream_key(logs.identity(before)), logs.stream_key(logs.identity(restarted)))
        self.assertNotEqual(logs.stream_key(logs.identity(before)), logs.stream_key(logs.identity(replaced)))

    def test_initial_follower_reads_only_current_lifetime_not_all_prior_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            collector = object.__new__(logs.Collector)
            collector.output, collector.rows, collector.live, collector.seen = Path(raw), [], {}, set()
            row = logs.identity(self.item())
            with mock.patch.object(logs.subprocess, 'Popen') as spawn:
                collector.attach(row)
            try:
                self.assertIn('--since', spawn.call_args.args[0])
                self.assertIn(row['started_at'], spawn.call_args.args[0])
                self.assertNotIn('--timestamps', spawn.call_args.args[0])
            finally:
                for _, stream, _ in collector.live.values():
                    stream.close()

    def test_only_owned_evidence_output_and_stop_paths_are_allowed(self):
        logs.validate_paths(logs.ROOT / 'evidence/stream-a', logs.ROOT / 'evidence/stream-a/stop')
        with self.assertRaises(ValueError):
            logs.validate_paths(Path('/tmp/logs'), logs.ROOT / 'evidence/stop')
        with self.assertRaises(ValueError):
            logs.validate_paths(logs.ROOT / 'evidence/logs', Path('/tmp/stop'))

    def exited_collector(self, latest):
        before = logs.identity(self.item())
        key = logs.stream_key(before)
        collector = object.__new__(logs.Collector)
        collector.duration = 1
        collector.stop = mock.Mock(exists=mock.Mock(return_value=False))
        collector.live = {key: (mock.Mock(poll=mock.Mock(return_value=0)), None, before)}
        collector.seen = {key}
        collector.report = {}
        discoveries = iter([[before]])
        collector.discover = mock.Mock(side_effect=lambda **_: next(discoveries, [latest]))
        collector.close_stream = mock.Mock(side_effect=lambda key, reason: collector.live.pop(key))
        collector.attach = mock.Mock()
        collector.persist = mock.Mock()
        return collector

    def test_planned_stop_between_discovery_and_follower_poll_is_not_monitor_failure(self):
        stopped = logs.identity(self.item())
        stopped['running'] = False
        collector = self.exited_collector(stopped)
        with mock.patch.object(logs.time, 'monotonic', return_value=0), \
                mock.patch.object(logs.time, 'sleep', side_effect=lambda _: collector.stop.exists.configure_mock(return_value=True)):
            collector.run()
        self.assertEqual(collector.report['state'], 'completed')
        self.assertEqual(collector.close_stream.call_args.args[1], 'container_stopped')
        self.assertEqual(collector.discover.call_count, 2)

    def test_new_container_lifetime_after_discovery_attaches_its_new_identity(self):
        restarted = logs.identity(self.item())
        restarted['started_at'] = '2026-10-16T00:01:00Z'
        collector = self.exited_collector(restarted)
        with mock.patch.object(logs.time, 'monotonic', return_value=0), \
                mock.patch.object(logs.time, 'sleep', side_effect=lambda _: collector.stop.exists.configure_mock(return_value=True)):
            collector.run()
        self.assertEqual(collector.close_stream.call_args.args[1], 'container_removed_or_new_lifetime')
        collector.attach.assert_called_once_with(restarted)

    def test_exited_follower_of_still_running_same_lifetime_remains_fatal(self):
        collector = self.exited_collector(logs.identity(self.item()))
        with mock.patch.object(logs.time, 'monotonic', return_value=0), mock.patch.object(logs.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'remained running'):
                collector.run()
        self.assertEqual(collector.report['state'], 'failed')

    def test_stop_visibility_can_lag_more_than_one_refresh(self):
        running = logs.identity(self.item())
        stopped = {**running, 'running': False}
        collector = self.exited_collector(stopped)
        collector.discover.side_effect = [[running], [running], [stopped]]
        with mock.patch.object(logs.time, 'monotonic', return_value=0), \
                mock.patch.object(logs.time, 'sleep', side_effect=lambda _: collector.stop.exists.configure_mock(return_value=True)):
            collector.run()
        self.assertEqual(collector.report['state'], 'completed')
        self.assertEqual(collector.close_stream.call_args.args[1], 'container_stopped')

    def test_partial_inspect_cannot_hide_a_still_discoverable_container(self):
        collector = object.__new__(logs.Collector)
        collector.report = {}
        result = lambda stdout, code=0: mock.Mock(stdout=stdout, returncode=code)
        with mock.patch.object(logs.subprocess, 'run', side_effect=[
                result('a' * 64), result('[]', 1), result('a' * 64)]):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                collector.discover()

    def test_discovery_timeout_is_bounded_and_explicit(self):
        collector = object.__new__(logs.Collector)
        with mock.patch.object(logs.subprocess, 'run', side_effect=logs.subprocess.TimeoutExpired('docker', 5)) as command:
            with self.assertRaises(logs.subprocess.TimeoutExpired):
                collector.discover()
        self.assertLessEqual(command.call_args.kwargs['timeout'], 5)
        self.assertEqual(command.call_count, 2)

    def test_one_transient_discovery_timeout_keeps_followers_and_recovers_within_deadline(self):
        collector = object.__new__(logs.Collector)
        collector.report, collector.live = {}, {'existing': object()}
        existing = collector.live['existing']
        item = self.item()
        result = lambda value: mock.Mock(returncode=0, stdout=value)
        with mock.patch.object(logs.time, 'monotonic', side_effect=[100, 100, 105, 105]), \
                mock.patch.object(logs.subprocess, 'run', side_effect=[
                    logs.subprocess.TimeoutExpired('docker', 5), result(item['Id']),
                    result(json.dumps([item]))]) as command:
            self.assertEqual(collector.discover(), [logs.identity(item)])
        self.assertIs(collector.live['existing'], existing)
        self.assertEqual([call.kwargs['timeout'] for call in command.call_args_list], [5, 5, 5])
        self.assertEqual(collector.report['discovery_timeouts'][0]['attempt'], 1)

    def test_discovery_retry_cannot_extend_the_original_ten_second_budget(self):
        collector = object.__new__(logs.Collector)
        collector.report = {}
        with mock.patch.object(logs.time, 'monotonic', side_effect=[100, 100, 110]), \
                mock.patch.object(logs.subprocess, 'run', side_effect=logs.subprocess.TimeoutExpired('docker', 5)) as command, \
                self.assertRaisesRegex(TimeoutError, 'budget exhausted'):
            collector.discover()
        self.assertEqual(command.call_count, 1)

    def test_long_utf8_json_log_bytes_are_preserved_without_fragment_timestamps(self):
        import tempfile
        payload = (json.dumps({'request_id': 'r', 'sql': '中文' * 10000}, ensure_ascii=False) + '\n').encode()
        with tempfile.TemporaryDirectory() as raw:
            collector = object.__new__(logs.Collector)
            collector.output, collector.rows, collector.live, collector.seen = Path(raw), [], {}, set()
            def write_log(command, stdout, **kwargs):
                self.assertNotIn('--timestamps', command)
                for offset in range(0, len(payload), 16384):
                    stdout.write(payload[offset:offset + 16384])
                return mock.Mock(poll=mock.Mock(return_value=0), returncode=0)
            row = logs.identity(self.item())
            with mock.patch.object(logs.subprocess, 'Popen', side_effect=write_log):
                collector.attach(row)
            collector.close_stream(logs.stream_key(row), 'test')
            self.assertEqual((Path(raw) / collector.rows[0]['file']).read_bytes(), payload)
            self.assertEqual(json.loads(payload)['request_id'], 'r')

    def test_inspection_uses_remaining_discovery_budget_without_retrying(self):
        collector = object.__new__(logs.Collector)
        collector.report = {}
        item = self.item()
        result = lambda stdout: mock.Mock(stdout=stdout, returncode=0)
        with mock.patch.object(logs.time, 'monotonic', side_effect=[100, 100, 101]), \
                mock.patch.object(logs.subprocess, 'run', side_effect=[
                    result(item['Id']), result(json.dumps([item]))]) as command:
            collector.discover()
        self.assertEqual(command.call_args_list[0].kwargs['timeout'], 5)
        self.assertEqual(command.call_args_list[1].kwargs['timeout'], 5)
        self.assertEqual(command.call_count, 2)

    def test_nonzero_follower_exit_is_incomplete_even_if_container_stopped(self):
        stopped = {**logs.identity(self.item()), 'running': False}
        collector = self.exited_collector(stopped)
        collector.discover = mock.Mock(return_value=[stopped])
        next(iter(collector.live.values()))[0].poll.return_value = 1
        with mock.patch.object(logs.time, 'monotonic', return_value=0), \
                mock.patch.object(logs.time, 'sleep', side_effect=lambda _: collector.stop.exists.configure_mock(return_value=True)), \
                self.assertRaisesRegex(RuntimeError, 'follower'):
            collector.run()
        self.assertEqual(collector.report['state'], 'failed')
        self.assertTrue(collector.report['coverage_gaps'])


if __name__ == '__main__':
    unittest.main()
