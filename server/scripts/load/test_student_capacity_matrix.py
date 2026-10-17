"""The matrix selects bounded experiments without manufacturing acceptance."""
import copy
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import run_student_capacity_matrix as matrix


class CapacityMatrixTests(unittest.TestCase):
    def test_v3_changes_reach_database_capture_core_teacher_and_pool_client_budget(self):
        keys = {'entry': 'ASSESSMENT_ENTRY_WORKERS', 'assessment': 'ASSESSMENT_REMAINING_WORKERS',
                'capture': 'ASSESSMENT_CAPTURE_WORKERS', 'web': 'ASSESSMENT_CORE_WORKERS',
                'teacher-read': 'ASSESSMENT_TEACHER_WORKERS'}
        config = {'services': {role: {'environment': {'GUNICORN_WORKERS': '1'}} for role in keys}}
        config['services'].update(db={'command': ['postgres', '-c', 'max_connections=64', '-c', 'fsync=on']},
                                  pgbouncer={'environment': {}}, nginx={'environment': {}})
        changes = {key: index + 20 for index, key in enumerate(keys.values())}
        changes.update(SCHOOL_DB_MAX_CONNECTIONS=160, PGBOUNCER_MAX_CLIENT_CONN=600,
                       ASSESSMENT_CAPTURE_DB_CONNECTIONS=24, ASSESSMENT_ENTRY_STARTUP_RATE=128)
        result = matrix.modified(config, changes)
        self.assertIn('max_connections=160', result['services']['db']['command'])
        self.assertIn('fsync=on', result['services']['db']['command'])
        for role, key in keys.items():
            self.assertEqual(result['services'][role]['environment']['GUNICORN_WORKERS'], str(changes[key]))
        self.assertEqual(result['services']['pgbouncer']['environment']['PGBOUNCER_MAX_CLIENT_CONN'], '600')
        self.assertEqual(result['services']['nginx']['environment']['ASSESSMENT_ENTRY_STARTUP_RATE'], '128')
        self.assertIn('max_connections=64', config['services']['db']['command'])

    def test_v3_parameters_include_every_pool_and_actual_resource_setting(self):
        env = {'ASSESSMENT_ENTRY_WORKERS': '40', 'ASSESSMENT_CAPTURE_WORKERS': '9',
               'ASSESSMENT_CAPTURE_DB_CONNECTIONS': '7', 'ASSESSMENT_CORE_DB_CONNECTIONS': '2',
               'ASSESSMENT_TEACHER_WORKERS': '2', 'PGBOUNCER_MAX_CLIENT_CONN': '512',
               'ASSESSMENT_REMAINING_DB_RESERVE_CONNECTIONS': '3', 'DATABASE_CONN_MAX_AGE': '0'}
        config = {'services': {'entry': {'environment': env, 'cpus': 0, 'mem_limit': 4294967296},
                               'db': {'command': ['postgres', '-c', 'max_connections=128']}}}
        values = matrix.parameters(config)
        for key, value in env.items():
            self.assertEqual(values.get(key), value, key)
        self.assertEqual(values.get('resource.entry.mem_limit'), '4294967296')

    def test_preload_wait_allows_only_bounded_new_lifetime_attachment(self):
        subject = object.__new__(matrix.Matrix)
        subject.check_diagnostics = mock.Mock(side_effect=[
            matrix.SafetyStop('diagnostic_log_lifetime_missing'), None])
        items = [{'Id': 'new-lifetime'}]
        with mock.patch.object(matrix.time, 'monotonic', return_value=0), \
                mock.patch.object(matrix.time, 'sleep') as sleep:
            subject.wait_for_diagnostic_coverage(items)
        self.assertEqual(subject.check_diagnostics.call_args_list, [mock.call(items), mock.call(items)])
        sleep.assert_called_once_with(0.25)
        subject.check_diagnostics.side_effect = matrix.SafetyStop('diagnostic_log_lifetime_missing')
        with mock.patch.object(matrix.time, 'monotonic', side_effect=[0, 10]), \
                mock.patch.object(matrix.time, 'sleep') as sleep, \
                self.assertRaisesRegex(matrix.SafetyStop, 'not_ready'):
            subject.wait_for_diagnostic_coverage(items)
        sleep.assert_not_called()

    def test_preload_wait_allows_initial_manifest_creation_but_not_an_invalid_existing_manifest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            subject = object.__new__(matrix.Matrix)
            subject.diagnostic_manifest = Path(raw) / 'manifest.json'
            subject.check_diagnostics = mock.Mock(side_effect=[
                matrix.SafetyStop('diagnostic_log_manifest_unavailable'), None])
            with mock.patch.object(matrix.time, 'sleep'):
                subject.wait_for_diagnostic_coverage([])
            self.assertEqual(subject.check_diagnostics.call_count, 2)
            subject.diagnostic_manifest.write_text('invalid')
            subject.check_diagnostics.side_effect = matrix.SafetyStop('diagnostic_log_manifest_unavailable')
            with self.assertRaisesRegex(matrix.SafetyStop, 'manifest_unavailable'):
                subject.wait_for_diagnostic_coverage([])

    def test_preload_wait_never_retries_failed_stale_or_gap_coverage(self):
        subject = object.__new__(matrix.Matrix)
        subject.check_diagnostics = mock.Mock(side_effect=matrix.SafetyStop('diagnostic_log_coverage_failed_or_stale'))
        with mock.patch.object(matrix.time, 'sleep') as sleep, \
                self.assertRaisesRegex(matrix.SafetyStop, 'failed_or_stale'):
            subject.wait_for_diagnostic_coverage([])
        sleep.assert_not_called()

    def test_profile_coverage_handshake_precedes_fixture_and_load_commands(self):
        subject = object.__new__(matrix.Matrix)
        subject.check_diagnostics = mock.Mock()
        subject.install = mock.Mock()
        subject.inspect_runtime = mock.Mock(return_value=['new-runtime'])
        subject.wait_for_diagnostic_coverage = mock.Mock(side_effect=matrix.SafetyStop('not_ready'))
        subject.call = mock.Mock()
        cell = {'stage': 'write', 'candidate': 'B', 'count': 3, 'mode': 'login-mixed'}
        with self.assertRaisesRegex(matrix.SafetyStop, 'not_ready'):
            subject.execute(cell)
        subject.install.assert_called_once_with('B')
        subject.wait_for_diagnostic_coverage.assert_called_once_with(['new-runtime'])
        subject.call.assert_not_called()

    def test_safety_interruption_preserves_the_full_180_denominator_and_unknown_operations(self):
        subject = object.__new__(matrix.Matrix)
        cell = {'stage': 'write', 'count': 180, 'mode': 'login-mixed'}
        plan = {'count': 180, 'mode': 'login-mixed', 'run_id': 'r', 'plan_id': 'p',
            'version': matrix.protocol.VERSION, 'roster': [{'account_id': str(index), 'role': 'login'} for index in range(180)],
            'thresholds': matrix.protocol.THRESHOLDS, 'maximum_release_skew_ms': 1000}
        records = [{'account_id': str(index), 'plan_id': 'p', 'run_id': 'r', 'status': 'completed',
            'login_ms': 1, 'release_skew_ms': 1} for index in range(120)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'plan.json').write_text(json.dumps(plan))
            raw = '\n'.join(json.dumps(row) for row in records) + '\n{"unfinished":'
            (path / 'records.jsonl').write_text(raw)
            subject.preserve_interrupted_cell(path, cell, matrix.protocol, 'r', matrix.SafetyStop('runtime_monitor_missing_or_stale'))
            report = json.loads((path / 'interrupted-cell.json').read_text())
            self.assertEqual(report['counts'], {'planned': 180, 'completed': 120, 'failed': 0, 'missing': 60, 'invalid': 0})
            self.assertEqual(report['unparseable_record_lines'], 1)
            self.assertFalse(report['passed'])
            self.assertTrue(report['safety_stop'])
            self.assertEqual(report['summary']['login']['planned'], 180)
            self.assertIsNone(report['summary']['login']['p95_ms'])
            self.assertEqual((path / 'records.jsonl').read_text(), raw)

    def test_fresh_file_cannot_rejuvenate_a_sample_that_blocked_over_30_seconds(self):
        subject = object.__new__(matrix.Matrix)
        subject.inspect_runtime = mock.Mock(return_value=[])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            output.with_suffix('.progress.json').write_text(json.dumps({'latest_sample': {
                'state': 'sampled', 'at': 100, 'sample_started_at': 63,
                'sample_duration_seconds': 37, 'memory_available_bytes': 8 * 1024**3}}))
            with mock.patch.object(matrix.time, 'time', return_value=100), \
                    self.assertRaisesRegex(matrix.SafetyStop, 'missing_or_stale'):
                subject.check_monitor(mock.Mock(poll=mock.Mock(return_value=None)), output,
                    matrix.SafetyWindow(), {}, 0)

    def test_new_failed_sample_cannot_be_hidden_by_prior_complete_sample(self):
        subject = object.__new__(matrix.Matrix)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            output.with_suffix('.progress.json').write_text(json.dumps({
                'heartbeat_at': 100, 'last_complete_sample_at': 99,
                'latest_sample': {'state': 'sampled', 'at': 99, 'sample_started_at': 98,
                    'sample_duration_seconds': 1, 'memory_available_bytes': 8 * 1024**3},
                'current_sample': {'state': 'failed', 'at': 100}}))
            with mock.patch.object(matrix.time, 'time', return_value=100), \
                    self.assertRaisesRegex(matrix.SafetyStop, 'measurement_failed'):
                subject.check_monitor(mock.Mock(poll=mock.Mock(return_value=None)), output,
                    matrix.SafetyWindow(), {}, 0)

    def test_partial_heartbeat_cannot_extend_last_complete_sample_age(self):
        subject = object.__new__(matrix.Matrix)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            output.with_suffix('.progress.json').write_text(json.dumps({
                'heartbeat_at': 100, 'last_complete_sample_at': 68,
                'latest_sample': {'state': 'sampled', 'at': 68, 'sample_started_at': 67,
                    'sample_duration_seconds': 1},
                'current_sample': {'state': 'partial', 'at': 100}}))
            with mock.patch.object(matrix.time, 'time', return_value=100), \
                    self.assertRaisesRegex(matrix.SafetyStop, 'missing_or_stale'):
                subject.check_monitor(mock.Mock(poll=mock.Mock(return_value=None)), output,
                    matrix.SafetyWindow(), {}, 0)

    def test_log_coverage_requires_current_following_lifetimes_and_successful_discovery(self):
        subject = object.__new__(matrix.Matrix)
        item = {'Id': 'a' * 64, 'State': {'StartedAt': 'start', 'Running': True},
            'Config': {'Labels': {'com.docker.compose.project': matrix.PROJECT,
                'com.docker.compose.service': 'entry'}}}
        manifest = {'project': matrix.PROJECT, 'state': 'collecting', 'updated_at': 100,
            'coverage_gaps': [], 'streams': [{'container_id': item['Id'], 'started_at': 'start',
                'state': 'following', 'history': 'since_container_start'}]}
        with tempfile.TemporaryDirectory() as directory:
            subject.diagnostic_manifest = Path(directory) / 'manifest.json'
            for change in ({'state': 'failed'}, {'updated_at': 69}, {'coverage_gaps': [{}]},
                    {'streams': []}, {'streams': [{**manifest['streams'][0], 'state': 'closed', 'sha256': 'valid'}]},
                    {'streams': [{**manifest['streams'][0], 'started_at': 'old'}]}):
                subject.diagnostic_manifest.write_text(json.dumps(manifest | change))
                with self.subTest(change=change), mock.patch.object(matrix.time, 'time', return_value=100), \
                        self.assertRaises(matrix.SafetyStop):
                    subject.check_diagnostics([item])
            subject.diagnostic_manifest.write_text(json.dumps(manifest))
            with mock.patch.object(matrix.time, 'time', return_value=100):
                subject.check_diagnostics([item])

    def test_failed_log_freshness_preserves_values_read_before_collector_cleanup(self):
        subject = object.__new__(matrix.Matrix)
        manifest = {'project': matrix.PROJECT, 'state': 'collecting', 'updated_at': 69,
            'coverage_gaps': [], 'streams': []}
        with tempfile.TemporaryDirectory() as directory:
            subject.diagnostic_manifest = Path(directory) / 'manifest.json'
            subject.diagnostic_manifest.write_text(json.dumps(manifest))
            with mock.patch.object(matrix.time, 'time', return_value=100), \
                    self.assertRaises(matrix.SafetyStop):
                subject.check_diagnostics()
        self.assertEqual(subject.diagnostic_check_failure, {
            'checked_at': 100, 'updated_at': 69, 'age_seconds': 31,
            'project': matrix.PROJECT, 'state': 'collecting', 'coverage_gaps': []})

    def test_log_freshness_uses_monotonic_clock_across_wall_clock_steps(self):
        subject = object.__new__(matrix.Matrix)
        manifest = {'project': matrix.PROJECT, 'state': 'collecting', 'updated_at': 100,
            'freshness_clock': 'monotonic', 'updated_at_monotonic': 20, 'coverage_gaps': [],
            'streams': [{'container_id': 'a', 'started_at': 'start', 'state': 'following',
                'history': 'since_container_start'}]}
        with tempfile.TemporaryDirectory() as directory:
            subject.diagnostic_manifest = Path(directory) / 'manifest.json'
            subject.diagnostic_manifest.write_text(json.dumps(manifest))
            for wall in (99.2, 200):
                with self.subTest(wall=wall), mock.patch.object(matrix.time, 'time', return_value=wall), \
                        mock.patch.object(matrix.time, 'monotonic', return_value=21):
                    subject.check_diagnostics()
            for mono in (19, 51):
                with self.subTest(mono=mono), mock.patch.object(matrix.time, 'time', return_value=100), \
                        mock.patch.object(matrix.time, 'monotonic', return_value=mono), \
                        self.assertRaises(matrix.SafetyStop):
                    subject.check_diagnostics()

    def test_resource_sample_freshness_uses_original_monotonic_start(self):
        subject = object.__new__(matrix.Matrix)
        subject.check_diagnostics = mock.Mock()
        subject.inspect_runtime = mock.Mock(return_value=[])
        latest = {'state': 'sampled', 'at': 100, 'sample_started_at': 100,
            'sample_started_monotonic': 20, 'sample_duration_seconds': 1,
            'memory_available_bytes': 8 * 1024**3}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.json'
            output.with_suffix('.progress.json').write_text(json.dumps({
                'freshness_clock': 'monotonic', 'heartbeat_monotonic': 21, 'latest_sample': latest}))
            with mock.patch.object(matrix.time, 'time', return_value=99.2), \
                    mock.patch.object(matrix.time, 'monotonic', return_value=21):
                subject.check_monitor(mock.Mock(poll=mock.Mock(return_value=None)), output,
                    matrix.SafetyWindow(), {}, 21)
            with mock.patch.object(matrix.time, 'time', return_value=100), \
                    mock.patch.object(matrix.time, 'monotonic', return_value=51), \
                    self.assertRaisesRegex(matrix.SafetyStop, 'missing_or_stale'):
                subject.check_monitor(mock.Mock(poll=mock.Mock(return_value=None)), output,
                    matrix.SafetyWindow(), {}, 51)

    def test_each_candidate_has_small_functional_checks_before_fifty_and_180(self):
        subject = object.__new__(matrix.Matrix)
        subject.report, subject.results = {}, []
        subject.save_report = lambda: None
        observed = []
        def execute(cell):
            observed.append((cell['candidate'], cell['count'], cell['mode']))
            return {**cell, 'passed': True, 'safety_stop': False}
        subject.execute = execute
        rows = subject.stage('write', ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))
        self.assertEqual(observed[:6], [('B', 3, 'login-mixed'), ('B', 3, 'startup-mixed'),
            ('B', 50, 'login-mixed'), ('B', 50, 'startup-mixed'),
            ('B', 180, 'login-mixed'), ('B', 180, 'startup-mixed')])
        self.assertEqual(observed[6:8], [('W8D4', 3, 'login-mixed'), ('W8D4', 3, 'startup-mixed')])
        self.assertEqual(len(rows), 8)
        self.assertEqual(len(subject.results), 12)

    def test_stage_schedule_preserves_factor_order_and_denominators(self):
        cells = matrix.cells('write', matrix.WRITE_CANDIDATES, ('login-mixed', 'startup-mixed'))
        self.assertEqual(len(cells), 20)
        self.assertEqual([(cell['candidate'], cell['count'], cell['mode']) for cell in cells[:4]],
            [('B', 50, 'login-mixed'), ('B', 50, 'startup-mixed'),
             ('B', 180, 'login-mixed'), ('B', 180, 'startup-mixed')])
        self.assertEqual({cell['count'] for cell in cells}, {50, 180})
        final = matrix.cells('final', ['selected'], matrix.ALL_MODES, counts=(180,), rounds=2)
        self.assertEqual(len(final), 16)
        self.assertEqual({cell['round'] for cell in final}, {1, 2})

    def test_selection_prefers_smallest_fully_passing_candidate(self):
        expected = matrix.cells('write', ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))
        results = [{**cell, 'passed': True, 'score_ms': 100 if cell['candidate'] == 'B' else 50}
            for cell in expected]
        self.assertEqual(matrix.select_candidate(results, ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))['candidate'], 'B')
        results[0]['passed'] = False
        self.assertEqual(matrix.select_candidate(results, ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))['candidate'], 'W8D4')
        for row in results:
            row['passed'] = False
        selected = matrix.select_candidate(results, ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))
        self.assertEqual(selected['candidate'], 'W8D4')
        self.assertFalse(selected['qualified'])

    def test_missing_cell_or_incomplete_denominator_never_passes(self):
        summary = {'thresholds_met': True, 'counts': {'planned': 180, 'completed': 179, 'missing': 1, 'failed': 0, 'invalid': 0}}
        verification = {'integrity_passed': True, 'workflow_complete': True, 'safety_stop': False, 'stats_ready': True}
        verdict = matrix.cell_verdict(summary, verification, monitor_ok=True)
        self.assertFalse(verdict['passed'])
        self.assertFalse(verdict['safety_stop'])
        summary['counts'].update(completed=180, missing=0)
        self.assertTrue(matrix.cell_verdict(summary, verification, monitor_ok=True)['passed'])
        verification['safety_stop'] = True
        self.assertTrue(matrix.cell_verdict(summary, verification, monitor_ok=True)['safety_stop'])
        selected = matrix.select_candidate([{'candidate': 'B', 'count': 180, 'mode': 'login-mixed', 'passed': True, 'score_ms': 1}],
            ('B',), ('login-mixed', 'startup-mixed'))
        self.assertFalse(selected['qualified'])

    def test_memory_guard_requires_three_spaced_samples_but_restart_stops_immediately(self):
        guard = matrix.SafetyWindow()
        for at in (0, 1, 14, 15, 16, 29):
            self.assertIsNone(guard.sample(at, 1024))
        self.assertEqual(guard.sample(30, 1024), 'memory_below_2GiB_three_15s_samples')
        self.assertEqual(matrix.SafetyWindow().sample(0, 8 * 1024**3, restarted=True), 'unexpected_container_restart')
        self.assertEqual(matrix.SafetyWindow().sample(0, None), 'memory_monitor_missing')

    def test_compose_guard_rejects_other_projects_external_volumes_and_ports(self):
        config = {'name': matrix.PROJECT, 'services': {'db': {'environment': {'POSTGRES_DB': 'kt_e2e'}},
            'nginx': {'ports': [{'host_ip': '127.0.0.1', 'published': str(matrix.PORT), 'target': 80}]}}}
        path = matrix.ROOT / 'private/compose-B.json'
        matrix.validate_compose(config, path)
        for change in ('project', 'volume', 'port', 'database', 'path'):
            altered = copy.deepcopy(config)
            candidate = path
            if change == 'project':
                altered['name'] = 'production'
            elif change == 'volume':
                altered['volumes'] = {'db': {'external': True, 'name': 'production_data'}}
            elif change == 'port':
                altered['services']['nginx']['ports'][0]['published'] = '80'
            elif change == 'database':
                altered['services']['db']['environment']['POSTGRES_DB'] = 'kt_platform'
            else:
                candidate = Path('/etc/compose.json')
            with self.subTest(change=change), self.assertRaises(ValueError):
                matrix.validate_compose(altered, candidate)

    def test_final_and_exploration_require_full_qualification(self):
        rows = [{**cell, 'passed': True} for cell in matrix.cells('qualification', ['selected'], matrix.ALL_MODES, counts=(180,))]
        self.assertTrue(matrix.all_modes_passed(rows))
        self.assertFalse(matrix.all_modes_passed(rows[:-1]))
        rows[0]['passed'] = False
        self.assertFalse(matrix.all_modes_passed(rows))

    def test_legacy_control_uses_old_driver_and_old_summary_version(self):
        old, filename = matrix.protocol_for_cell({'stage': 'legacy-control', 'candidate': 'B',
            'count': 50, 'mode': 'login-mixed', 'transport_protocol': 'legacy-v2'})
        self.assertEqual(filename, 'student_concurrency_protocol.py')
        self.assertEqual(old.VERSION, 'student-concurrency-protocol-v2')
        plan = {'count': 1, 'plan_id': 'p', 'run_id': 'r', 'mode': 'login',
            'roster': [{'account_id': 'a', 'role': 'login'}], 'thresholds': old.THRESHOLDS,
            'maximum_release_skew_ms': 1000}
        summary = old.summarize(plan, [])
        self.assertEqual(summary['version'], old.VERSION)
        self.assertNotIn('processing', summary)
        current, filename = matrix.protocol_for_cell({'stage': 'write', 'count': 50, 'mode': 'login-mixed'})
        self.assertEqual(filename, 'student_capacity_protocol.py')
        self.assertEqual(current.VERSION, 'student-capacity-protocol-v2')

    def test_legacy_control_stage_has_only_four_cells_and_skips_small_preflight(self):
        subject = object.__new__(matrix.Matrix)
        subject.report, subject.results = {}, []
        subject.save_report = lambda: None
        seen = []
        def execute(cell):
            seen.append(cell)
            return {**cell, 'passed': True, 'safety_stop': False}
        subject.execute = execute
        rows = subject.stage('legacy-control', ('B',), ('login-mixed', 'startup-mixed'), transport_protocol='legacy-v2')
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['count'] for row in seen}, {50, 180})
        self.assertTrue(all(row['transport_protocol'] == 'legacy-v2' for row in seen))
        self.assertEqual(len(subject.results), 4)

    def test_legacy_rows_cannot_select_a_candidate_or_pass_qualification(self):
        legacy = [{**cell, 'passed': True, 'score_ms': 1, 'transport_protocol': 'legacy-v2'}
            for cell in matrix.cells('legacy-control', ('B',), ('login-mixed', 'startup-mixed'))]
        new = [{**cell, 'passed': True, 'score_ms': 100}
            for cell in matrix.cells('write', ('W8D4',), ('login-mixed', 'startup-mixed'))]
        selected = matrix.select_candidate(legacy + new, ('B', 'W8D4'), ('login-mixed', 'startup-mixed'))
        self.assertEqual(selected['candidate'], 'W8D4')
        final = [{**cell, 'passed': True, 'transport_protocol': 'legacy-v2'}
            for cell in matrix.cells('legacy-control', ('B',), matrix.ALL_MODES, counts=(180,))]
        self.assertFalse(matrix.all_modes_passed(final))

    def test_legacy_control_cannot_expand_to_other_modes_or_enter_qualification(self):
        base = {'stage': 'legacy-control', 'candidate': 'B', 'count': 50, 'mode': 'login-mixed', 'transport_protocol': 'legacy-v2'}
        for changes in ({'stage': 'qualification'}, {'candidate': 'W8D4'}, {'count': 3}, {'mode': 'submission'}, {'transport_protocol': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                matrix.protocol_for_cell(base | changes)


if __name__ == '__main__':
    unittest.main()
