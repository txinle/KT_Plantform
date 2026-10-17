"""Retest ownership, exact pressure denominator and time budget contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).parent))


class RetestContractTests(unittest.TestCase):
    def test_frozen_identity_binds_the_separate_measurement_archive(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.manifest = {'build_source_sha256': 'application', 'measurement_archive_sha256': 'measurement'}
        probe.configs, probe.fixture_bytes = {'C1': (None, {})}, b'fixture'
        self.assertEqual(probe.evidence_identity('C1')['measurement_archive_sha256'], 'measurement')

    def test_diagnostics_exposes_malformed_json_instead_of_silently_skipping_it(self):
        import run_student_capacity_retest as controller
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / 'nginx.log').write_text(json.dumps({'at': 10, 'request_id': 'one', 'status': 200}) + '\n')
            (directory / 'app.log').write_text(json.dumps({'at': 10, 'request_id': 'one',
                'wsgi_application_seconds': .2}) + '\n{"request_id": broken}\n')
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'streams': [{'file': 'nginx.log', 'service': 'nginx'},
                {'file': 'app.log', 'service': 'assessment'}]}))
            result = controller.collect_diagnostics(directory, manifest, 0, 20)
            self.assertEqual(result['diagnostic_parse_counts']['json_candidates'], 3)
            self.assertEqual(result['diagnostic_parse_counts']['parsed'], 2)
            self.assertEqual(result['diagnostic_parse_counts']['failed'], 1)
            self.assertFalse(result['diagnostic_parse_complete'])
            self.assertEqual(result['request_correlation_coverage'], 1)
            self.assertIsNone(result['pool_waiting_max'])

    def test_current_controller_starts_with_c1_and_only_runs_full_flow(self):
        import run_student_capacity_retest as controller
        subject = self.subject()
        probe = object.__new__(controller.RetestMatrix)
        probe.configs, probe.selections, probe.report = {}, [], {}
        probe.save_report = Mock()
        probe.publish_controller = Mock()
        probe.stop_collector = Mock()
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            probe.root = Path(raw)
            (probe.root / 'private').mkdir()
            config = {'services': {'entry': {'environment': subject.r0_parameters()}}}
            (probe.root / 'private/compose-R0.json').write_text(json.dumps(config))
            probe.register = lambda name, value: probe.configs.update({name: (probe.root / name, value)})
            def stage(stage, names, modes=subject.MODES, **kwargs):
                calls.append((stage, names, modes))
                return [{**cell, 'passed': True, 'browser_passed': True, 'generator_valid': True,
                    'monitor_ok': True, 'integrity_passed': True, 'full_denominator_completed': True,
                    'safety_stop': False, 'timings': {}, 'evidence_identity': {
                        'workload_profile': subject.WORKLOAD_PROFILE, 'configuration': names[0]}}
                    for cell in subject.retest_cells(stage, names, modes,
                        rounds=kwargs.get('rounds', 1))]
            probe.stage_v3 = stage
            probe.run()
        self.assertEqual(calls[0][1], ('C1',))
        self.assertTrue(all(modes == ('full-flow',) for _, _, modes in calls))
        self.assertTrue(probe.report['formal_capacity_accepted'])

    def test_browser_measurement_flag_requires_actual_browser_observations(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.results, probe.report, probe.comparison_reuse = [], {}, {}
        probe.save_report = Mock()
        probe.evidence_identity = Mock(return_value={})
        probe.execute = Mock(side_effect=lambda cell: {**cell, 'passed': True,
            'safety_stop': False, 'counts': {}, 'browser_observations': [] if not cell['browser_count'] else [
                {'driver': 'desktop'}, {'driver': 'mobile_emulation'}]})
        probe.stage_v3('qualification', ('R0',), ('full-flow',), counts=(50,))
        self.assertFalse(probe.report['browser_operability_measured'])
        probe.stage_v3('final', ('R0',), ('full-flow',), counts=(50,))
        self.assertTrue(probe.report['browser_operability_measured'])

    def test_finalization_keeps_the_first_interruption_and_records_the_second_failure(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.preserve_interrupted_cell = Mock(side_effect=FileExistsError)
        primary = controller.previous.SafetyStop('runtime_monitor_measurement_failed')
        final = controller.previous.SafetyStop('diagnostic_collection_finalization_failed')
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            original = directory / 'interrupted-cell.json'
            original.write_text('{"primary": true}')
            probe.preserve_finalization_failure(directory, {'count': 180}, 'cell', final, primary)
            self.assertEqual(original.read_text(), '{"primary": true}')
            report = json.loads((directory / 'finalization-failure.json').read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['primary_failure_reason'], str(primary))
            self.assertEqual(report['failure_reason'], str(final))
        probe.preserve_interrupted_cell.assert_not_called()

    def test_finalization_without_an_earlier_failure_preserves_an_interrupted_cell(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.preserve_interrupted_cell = Mock()
        error = controller.previous.SafetyStop('diagnostic_collection_finalization_failed')
        probe.preserve_finalization_failure(Path('cell'), {'count': 50}, 'cell', error, None)
        probe.preserve_interrupted_cell.assert_called_once_with(Path('cell'), {'count': 50},
            controller.protocol, 'cell', error)

    def test_fully_observed_capacity_failure_is_cached_without_turning_it_into_a_pass(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.results, probe.report, probe.comparison_reuse = [], {}, {}
        probe.configs = {'R0': (Path('unused'), {'services': {}})}
        probe.manifest, probe.fixture_bytes = {'build_source_sha256': 'r5'}, b'frozen'
        probe.save_report = Mock()
        def execute(cell):
            return {**cell, 'passed': False, 'safety_stop': False, 'generator_valid': True,
                'monitor_ok': True, 'integrity_passed': False, 'full_denominator_completed': False,
                'counts': {'planned': cell['count'], 'completed': 0, 'failed': cell['count'], 'missing': 0, 'invalid': 0},
                'evidence_identity': probe.evidence_identity('R0')}
        probe.execute = Mock(side_effect=execute)
        probe.stage_v3('qualification', ('R0',), ('full-flow',))
        cached = probe.stage_v3('qualification', ('R0',), ('full-flow',))
        self.assertEqual(probe.execute.call_count, 2)
        self.assertTrue(all(row['passed'] is False for row in cached))

    def test_saving_pressure_is_addressed_before_downstream_submission_queue(self):
        import run_student_capacity_retest as controller
        row = {'generator_valid': True, 'timings': {'save_scheduled': {'p95_ms': 17500},
            'processing': {'p95_ms': 95000}}, 'diagnostics': {'saving_cpu_busy': .55,
            'assessment_pool_waiting_max': 4, 'assessment_queue_p95_ms': 5200}}
        self.assertEqual(controller.bottleneck_dimension(row), 'assessment-pool')
        row['timings'] = {'save_scheduled': {'planned': 60480, 'missing': 35406, 'p95_ms': None, 'p99_ms': None}}
        self.assertEqual(controller.bottleneck_dimension(row), 'assessment-pool')

    def test_verification_checks_monitor_while_using_the_remaining_statistics_budget(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        check = Mock()
        process = Mock(returncode=0)
        process.poll.side_effect = [None, None, 0, 0]
        with tempfile.TemporaryDirectory() as raw, patch.object(controller.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(controller.time, 'sleep'):
            probe.verify_with_monitor('owned-web', ['verify_student_capacity_load', '--output', '/tmp/cell/out.json'],
                Path(raw) / 'verification.log', timeout=180, check=check)
        self.assertEqual(check.call_count, 2)
        command = spawn.call_args.args[0]
        self.assertIn('timeout', command)
        self.assertIn('180', command)

    def test_frozen_workload_budget_failure_is_not_a_generator_resource_failure(self):
        import run_student_capacity_retest as controller
        done = {'state': 'driver_failed', 'generator_valid': True, 'error': {
            'code': 'TimeoutError', 'message': 'Driver workload exceeded the frozen phase bounds'}}
        self.assertTrue(controller.driver_measurement_valid(done))
        self.assertFalse(controller.driver_measurement_valid(done | {'generator_valid': False}))
        self.assertFalse(controller.driver_measurement_valid(done | {'error': {'code': 'TimeoutError', 'message': 'Other timeout'}}))
        self.assertFalse(controller.driver_measurement_valid(done | {'error': None}))

    def test_incomplete_workflow_does_not_repeat_expensive_statistics_reconciliation(self):
        import run_student_capacity_retest as controller
        failed = {'workflow_complete': False, 'integrity_passed': False, 'stats_ready': False, 'safety_stop': False}
        self.assertFalse(controller.statistics_retry_needed(failed))
        self.assertFalse(controller.statistics_retry_needed(failed | {'workflow_complete': True, 'stats_ready': True}))
        self.assertTrue(controller.statistics_retry_needed(failed | {'workflow_complete': True}))
        self.assertTrue(controller.statistics_retry_needed({'stats_ready': False}))

    def test_verifier_uses_owned_entry_memory_headroom_and_rejects_missing_or_exhausted_budget(self):
        import run_student_capacity_retest as controller
        gib = 1024**3
        item = {'Id': 'entry', 'State': {'Running': True}, 'HostConfig': {'Memory': 4 * gib},
            'Config': {'Labels': {'com.docker.compose.project': 'owned', 'com.docker.compose.service': 'entry'}}}
        sample = {'memory_total_bytes': 16 * gib, 'memory_available_bytes': 7 * gib,
            'resource_rows': [{'scope': 'isolated_test', 'role': 'entry', 'working_set_bytes': gib}]}
        selected = controller.verification_target([item], sample, 'owned', 250 * 1024**2)
        self.assertEqual(selected['container_id'], 'entry')
        self.assertEqual(selected['estimated_extra_memory_bytes'], 2000 * 1024**2)
        with self.assertRaises(controller.previous.SafetyStop):
            controller.verification_target([item | {'HostConfig': {'Memory': 2 * gib}}], sample, 'owned', 250 * 1024**2)
        with self.assertRaises(controller.previous.SafetyStop):
            controller.verification_target([item], sample | {'resource_rows': []}, 'owned', 1)
        with self.assertRaises(controller.previous.SafetyStop):
            controller.verification_target([item], sample | {'memory_available_bytes': 3 * gib}, 'owned', 250 * 1024**2)
        with self.assertRaises(controller.previous.SafetyStop):
            controller.verification_target([item], sample, 'another-project', 1)

    def test_result_resume_requires_the_frozen_identity_and_a_finalized_cell_file(self):
        import run_student_capacity_retest as controller
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / 'evidence/matrix-old/cells/one'
            directory.mkdir(parents=True)
            (root / 'private').mkdir()
            config = {'name': 'owned-project', 'services': {}}
            config_path = root / 'private/compose-matrix-old-R0.json'
            config_path.write_text(json.dumps(config))
            import hashlib
            identity = {'application': 'r5', 'configuration': controller.protocol.digest(config),
                        'baseline': hashlib.sha256(b'frozen').hexdigest(), 'measurement': 'v4r1', 'driver_sha256': 'driver'}
            row = {'candidate': 'R0', 'stage': 'qualification', 'workload': 'paper-20q', 'count': 50, 'mode': 'full-flow',
                   'evidence_identity': identity, 'evidence': 'evidence/matrix-old/cells/one', 'safety_stop': False,
                   'monitor_ok': True, 'generator_valid': True, 'integrity_passed': True, 'stats_ready': True,
                   'full_denominator_completed': True}
            (directory / 'cell-result.json').write_text(json.dumps(row))
            (directory / 'fixture.json').write_bytes(b'frozen')
            report = root / 'evidence/matrix-old/matrix-report.json'
            report.write_text(json.dumps({'project': 'owned-project', 'run_id': 'old', 'state': 'completed', 'cells': [row]}))
            self.assertEqual(controller.load_result_resume(root, report, b'frozen', identity, 'owned-project'), [row])
            forwarded = root / 'evidence/matrix-new/matrix-report.json'
            forwarded.parent.mkdir()
            forwarded.write_text(json.dumps({'project': 'owned-project', 'run_id': 'new', 'state': 'completed', 'cells': [row]}))
            self.assertEqual(controller.load_result_resume(root, forwarded, b'frozen', identity, 'owned-project'), [row])
            other_config = {'name': 'owned-project', 'services': {'assessment': {'workers': 15}}}
            (root / 'private/compose-matrix-old-C1.json').write_text(json.dumps(other_config))
            other_directory = root / 'evidence/matrix-old/cells/two'
            other_directory.mkdir()
            other = row | {'candidate': 'C1', 'evidence': 'evidence/matrix-old/cells/two',
                'evidence_identity': identity | {'configuration': controller.protocol.digest(other_config)}}
            (other_directory / 'fixture.json').write_bytes(b'frozen')
            (other_directory / 'cell-result.json').write_text(json.dumps(other))
            forwarded.write_text(json.dumps({'project': 'owned-project', 'run_id': 'new', 'state': 'completed', 'cells': [row, other]}))
            self.assertEqual(controller.load_result_resume(root, forwarded, b'frozen', identity, 'owned-project'), [row, other])
            self.assertEqual(controller.load_result_resume(root, report, b'frozen', identity | {'measurement': 'changed'}, 'owned-project'), [])
            (directory / 'cell-result.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'finalized'):
                controller.load_result_resume(root, report, b'frozen', identity, 'owned-project')

    def test_candidate_cannot_hide_navigation_or_tail_regression_in_a_sum(self):
        subject = self.subject()
        def row(count):
            return {'count': count, 'mode': 'full-flow', 'generator_valid': True, 'monitor_ok': True,
                    'integrity_passed': True, 'full_denominator_completed': True, 'safety_stop': False,
                    'timings': {'save_scheduled': {'planned': 10, 'observed': 10, 'p95_ms': 4000, 'p99_ms': 8000},
                                'navigation': {'planned': 10, 'observed': 10, 'p95_ms': 900, 'p99_ms': 2000}}}
        old = [row(50), row(180)]
        new = copy.deepcopy(old)
        for item in new:
            item['timings']['save_scheduled'].update(p95_ms=2000, p99_ms=4000)
        self.assertTrue(subject.compare_candidate(old, new, 'assessment-pool')['improved'])
        new[1]['timings']['navigation']['p99_ms'] = 4000
        self.assertFalse(subject.compare_candidate(old, new, 'assessment-pool')['improved'])
        self.assertFalse(subject.compare_candidate(old, new[:1], 'assessment-pool')['improved'])

    def test_candidate_can_reach_sla_after_an_incomplete_baseline_without_inventing_a_percent_gain(self):
        subject = self.subject()
        old = [{'count': count, 'mode': 'full-flow', 'timings': {
            'save_scheduled': {'planned': 10, 'observed': 4, 'missing': 6, 'p95_ms': None, 'p99_ms': None},
            'statistics': {'planned': 4, 'observed': 4, 'max_ms': 90000}}} for count in (50, 180)]
        new = [{'count': count, 'mode': 'full-flow', 'generator_valid': True, 'monitor_ok': True,
            'integrity_passed': True, 'full_denominator_completed': True, 'safety_stop': False,
            'timings': {'save_scheduled': {'planned': 10, 'observed': 10, 'p95_ms': 900, 'p99_ms': 2000},
                'statistics': {'planned': 10, 'observed': 10, 'max_ms': 120000}}} for count in (50, 180)]
        decision = subject.compare_candidate(old, new, 'assessment-pool')
        self.assertTrue(decision['improved'], decision['issues'])
        self.assertTrue(all(row['change_fraction'] is None for row in decision['metrics']))
        new[1]['timings']['save_scheduled']['p95_ms'] = 2000
        self.assertFalse(subject.compare_candidate(old, new, 'assessment-pool')['improved'])

    def test_formal_must_bind_one_frozen_identity_and_complete_monitoring(self):
        subject = self.subject()
        rows = [{**cell, 'passed': True, 'browser_passed': True, 'generator_valid': True,
                 'monitor_ok': True, 'evidence_identity': {'workload_profile': 'full-flow-20q-gradual-login-v1', 'application': 'r5', 'configuration': 'one',
                    'baseline': 'frozen', 'measurement': 'v4'}}
                for cell in subject.retest_cells('final', ['selected'], rounds=2)]
        self.assertTrue(subject.formal_passed(rows))
        rows[-1]['evidence_identity']['configuration'] = 'other'
        self.assertFalse(subject.formal_passed(rows))
        rows[-1]['evidence_identity']['configuration'] = 'one'
        rows[-1]['monitor_ok'] = False
        self.assertFalse(subject.formal_passed(rows))

    def test_valid_metric_cache_never_repeats_same_configuration_or_hides_changed_driver(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.results, probe.report, probe.comparison_reuse = [], {}, {}
        probe.configs = {'R0': (Path('unused'), {'services': {}})}
        probe.manifest = {'build_source_sha256': 'r5'}
        probe.fixture_bytes = b'frozen'
        probe.save_report = Mock()
        def execute(cell):
            return {**cell, 'passed': False, 'safety_stop': False, 'generator_valid': True,
                    'monitor_ok': True, 'integrity_passed': True, 'full_denominator_completed': True,
                    'counts': {}, 'evidence_identity': probe.evidence_identity('R0')}
        probe.execute = Mock(side_effect=execute)
        probe.stage_v3('qualification', ('R0',), ('full-flow',))
        probe.stage_v3('qualification', ('R0',), ('full-flow',))
        self.assertEqual(probe.execute.call_count, 2)
        probe.configs['R0'] = (Path('unused'), {'services': {'changed': {}}})
        probe.stage_v3('qualification', ('R0',), ('full-flow',))
        self.assertEqual(probe.execute.call_count, 4)

    def test_coordinator_uses_its_frozen_sibling_driver_after_legacy_path_setup(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw)
            source=Path(__file__).parent
            for name in ('run_student_capacity_retest.py','student_capacity_protocol_v3.py'):
                shutil.copyfile(source/name,directory/name)
            code=('import sys;from pathlib import Path;sys.path[:0]=sys.argv[1:3];'
                  'import run_student_capacity_retest as c;'
                  'assert Path(c.protocol.__file__).parent==Path(sys.argv[1]),c.protocol.__file__')
            result=subprocess.run([sys.executable,'-c',code,str(directory),str(source)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_measured_cpu_joins_request_ids_and_excludes_preparation_without_cross_host_clock_subtraction(self):
        import run_student_capacity_retest as controller
        self.assertTrue(callable(getattr(controller, 'measured_phase_cpu', None)), 'Request-bound phase CPU missing')
        records = [{'role': 'login', 'login_ms': 9000, 'http_calls': [
            {'request_id': 'preparation', 'http_send_monotonic_ms': 1000},
            {'request_id': 'measured', 'http_send_monotonic_ms': 11000}]}]
        ingress = [{'request_id': 'preparation', 'at': 1005, 'request_time': 4},
                   {'request_id': 'measured', 'at': 1019, 'request_time': 8}]
        result = controller.measured_phase_cpu(records, ingress,
            [(1000, 1010, .2), (1010, 1020, .95), (1020, 1030, .2)], 10000)
        self.assertAlmostEqual(result['login_cpu_busy'], .95)
        self.assertEqual(result['phase_cpu_windows']['login']['matched_requests'], 1)

    def test_entry_cpu_and_capture_startup_are_used_for_their_actual_latency_failure(self):
        import run_student_capacity_retest as controller
        row = {'generator_valid': True, 'timings': {'login': {'p95_ms': 15000}},
               'diagnostics': {'cpu_busy': .3, 'login_cpu_busy': .95, 'login_rejected': 100}}
        self.assertEqual(controller.bottleneck_dimension(row), 'login-reduce')
        row.update(timings={'startup': {'p95_ms': 8500}},
                   diagnostics={'cpu_busy': .3, 'startup_cpu_busy': .5, 'capture_queue_p95_ms': 1500})
        self.assertEqual(controller.bottleneck_dimension(row), 'capture-workers')
        row['diagnostics']['startup_cpu_busy'] = .95
        self.assertIsNone(controller.bottleneck_dimension(row))

    def test_gradual_phase_cpu_uses_personal_actual_login_and_ready_times(self):
        import run_student_capacity_retest as controller
        records = [{'role': 'full-flow', 'workload_profile': 'full-flow-20q-gradual-login-v1',
            'login_ms': 2000, 'startup_ms': 1000, 'login_actual_monotonic_ms': 20000,
            'login_ready_monotonic_ms': 22000, 'workload_start_monotonic_ms': 23000,
            'http_calls': [{'request_id': 'login', 'http_send_monotonic_ms': 20000},
                           {'request_id': 'startup', 'http_send_monotonic_ms': 22001}]}]
        ingress = [{'request_id': 'login', 'at': 1022, 'request_time': 2},
                   {'request_id': 'startup', 'at': 1023, 'request_time': 1}]
        result = controller.measured_phase_cpu(records, ingress, [(1020, 1022, .4), (1022, 1023, .7)], 10000)
        self.assertAlmostEqual(result['login_cpu_busy'], .4)
        self.assertAlmostEqual(result['startup_cpu_busy'], .7)
        self.assertEqual(result['phase_cpu_windows']['login']['matched_requests'], 1)

    def test_cpu_counter_intervals_use_measurement_time_not_partial_or_complete_write_time(self):
        import run_student_capacity_retest as controller
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            samples = [{'at': 109, 'sample_started_at': 100, 'host_cpu_counters': [10, 0, 0, 90, 0]},
                       {'at': 110, 'sample_started_at': 110, 'host_cpu_counters': [15, 0, 0, 95, 0]}]
            (directory / 'observation.samples.jsonl').write_text('\n'.join(json.dumps(s) for s in samples))
            (directory / 'release.json').write_text(json.dumps({'release_monotonic': 10}))
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'streams': [{'file': 'nginx.jsonl', 'service': 'nginx'}]}))
            (directory / 'nginx.jsonl').write_text(json.dumps({'request_id': 'login', 'at': 109,
                'request_time': 8, 'status': 200}) + '\n')
            records = [{'role': 'login', 'login_ms': 9000,
                        'http_calls': [{'request_id': 'login', 'http_send_monotonic_ms': 11000}]}]
            result = controller.collect_diagnostics(directory, manifest, 100, 120, records=records)
            self.assertEqual(result['phase_cpu_windows']['login']['covered_seconds'], 8)

    def test_resume_comparisons_reuses_only_verified_same_fixture_config_and_driver(self):
        import hashlib
        import run_student_capacity_retest as controller
        self.assertTrue(callable(getattr(controller, 'load_comparison_resume', None)), 'Bound comparison resume missing')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / 'evidence/matrix-old/cells/one'
            directory.mkdir(parents=True)
            fixture = b'{"baseline":"unchanged"}'
            config = {'services': {'entry': {'environment': {}}}}
            row = {'stage': 'comparison', 'candidate': 'R0', 'count': 50, 'mode': 'login-mixed',
                   'workload': 'comparison-2q', 'generator_valid': True, 'safety_stop': False,
                   'integrity_passed': True, 'stats_ready': True, 'full_denominator_completed': True,
                   'evidence': 'evidence/matrix-old/cells/one', 'parameters': {}}
            (directory / 'cell-result.json').write_text(json.dumps(row))
            (directory / 'fixture.json').write_bytes(fixture)
            plan = {'fixture_sha256': hashlib.sha256(fixture).hexdigest(),
                'driver_sha256': hashlib.sha256(Path(controller.protocol.__file__).read_bytes()).hexdigest(),
                'version': controller.protocol.VERSION, 'mode': row['mode'], 'count': row['count'],
                'workload': row['workload']}
            plan['plan_id'] = controller.protocol.digest(plan)
            (directory / 'plan.json').write_text(json.dumps(plan))
            (directory / 'source-config.json').write_text(json.dumps({'parameters': {}}))
            (root / 'private').mkdir()
            (root / 'private/compose-matrix-old-R0.json').write_text(json.dumps(config))
            report = root / 'evidence/matrix-old/matrix-report.json'
            report.write_text(json.dumps({'project': 'owned-project', 'run_id': 'old', 'state': 'safety_stopped',
                'cells': [row, row | {'count': 180, 'generator_valid': False, 'safety_stop': True}]}))
            self.assertEqual(controller.load_comparison_resume(root, report, fixture, config, 'owned-project'), [row])
            with self.assertRaisesRegex(ValueError, 'fixture'):
                controller.load_comparison_resume(root, report, b'changed', config, 'owned-project')
            with self.assertRaisesRegex(ValueError, 'project'):
                controller.load_comparison_resume(root, report, fixture, config, 'other-project')
            (directory / 'source-config.json').write_text(json.dumps({'parameters': {'workers': 'changed'}}))
            with self.assertRaisesRegex(ValueError, 'configuration'):
                controller.load_comparison_resume(root, report, fixture, config, 'owned-project')

    def test_resumed_comparison_is_not_executed_again(self):
        import run_student_capacity_retest as controller
        row = {'stage': 'comparison', 'candidate': 'R0', 'count': 50, 'mode': 'full-flow',
               'passed': False, 'counts': {}, 'safety_stop': False}
        probe = object.__new__(controller.RetestMatrix)
        probe.results, probe.report = [row], {}
        probe.comparison_reuse = {(50, 'full-flow'): row}
        probe.save_report = Mock()
        probe.execute = Mock(side_effect=AssertionError('Already valid comparison repeated'))
        self.assertEqual(probe.stage_v3('comparison', ('R0',), ('full-flow',), counts=(50,)), [row])

    def test_repeated_reverse_controls_keep_distinct_execution_evidence(self):
        import run_student_capacity_retest as controller
        probe = object.__new__(controller.RetestMatrix)
        probe.results, probe.report = [], {}
        probe.save_report = Mock()
        identities = []
        def execute(cell):
            identities.append(json.dumps(cell, sort_keys=True))
            return {**cell, 'passed': True, 'counts': {}, 'safety_stop': False}
        probe.execute = execute
        for _ in range(2):
            probe.stage_v3('reverse-control', ('R0',), ('full-flow',), counts=(50,))
        self.assertEqual(len(set(identities)), 2)

    def subject(self):
        self.assertIsNotNone(importlib.util.find_spec('capacity_retest'), 'Retest contract is not implemented')
        import capacity_retest
        return capacity_retest

    def test_owned_manifest_cannot_reuse_old_project_volume_or_port(self):
        subject = self.subject()
        manifest = subject.default_manifest()
        self.assertEqual(subject.validate_manifest(manifest), manifest)
        for key, value in (('project', 'kt-school-e2e-capacity-20261016'),
                           ('root', '/home/ktdata/student-capacity-tests/20261016'), ('port', 18084)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                subject.validate_manifest(manifest | {key: value})

    def test_runtime_identity_is_read_from_the_same_validated_manifest_for_child_tools(self):
        subject = self.subject()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps(subject.default_manifest()))
            with patch.dict('os.environ', {subject.MANIFEST_ENV: str(path)}):
                root, project, port = subject.runtime_identity()
            self.assertEqual((str(root).replace('\\', '/'), project, port),
                (subject.ROOT, subject.PROJECT, subject.PORT))

    def test_remote_compose_rebinding_rejects_external_resources_and_old_bind_targets(self):
        import prepare_student_capacity_remote as prepare
        self.assertTrue(callable(getattr(prepare, 'rebind_retest_compose', None)), 'Retest compose isolation is missing')
        subject = self.subject()
        root = Path(subject.ROOT)
        config = {'name': 'unrendered', 'volumes': {'postgres_data': {}}, 'networks': {'default': {}},
                  'services': {'db': {'environment': {'POSTGRES_DB': 'kt_e2e'}, 'volumes': [
                      {'type': 'volume', 'source': 'postgres_data', 'target': '/var/lib/postgresql/data'}]},
                   'entry': {'environment': {'DJANGO_CSRF_TRUSTED_ORIGINS': 'http://localhost:18082'}},
                   'web': {'environment': {'DJANGO_CSRF_TRUSTED_ORIGINS': 'http://localhost:18080'}},
                   'nginx': {'ports': [{'host_ip': '127.0.0.1', 'published': '18084', 'target': 80}],
                       'volumes': [{'type': 'bind', 'source': str(root / 'source/deploy/nginx/kt-school-capacity.conf.template'),
                                    'target': '/etc/nginx/templates/default.conf.template'}]}}}
        result = prepare.rebind_retest_compose(config, subject.default_manifest(), 'backend:new', 'frontend:new')
        self.assertEqual(result['name'], subject.PROJECT)
        self.assertEqual(result['volumes']['postgres_data']['name'], subject.PROJECT + '_postgres_data')
        self.assertEqual(str(result['services']['nginx']['ports'][0]['published']), '18085')
        for role in ('entry', 'web'):
            self.assertEqual(result['services'][role]['environment']['DJANGO_CSRF_TRUSTED_ORIGINS'],
                             'http://127.0.0.1:18085,http://localhost:18085')
        config['services']['nginx']['volumes'][0]['source'] = '/home/ktdata/student-capacity-tests/20261016/source/x'
        with self.assertRaises(ValueError):
            prepare.rebind_retest_compose(config, subject.default_manifest(), 'b', 'f')

    def test_cold_clone_requires_stopped_owned_source_database_and_media(self):
        import prepare_student_capacity_remote as prepare
        self.assertTrue(callable(getattr(prepare, 'cold_source_volumes', None)), 'Stopped source clone guard is missing')
        project = 'kt-school-e2e-capacity-20261016'
        rows = [{'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': 'db'}},
                 'State': {'Running': False}, 'Mounts': [{'Type': 'volume', 'Name': project + '_postgres_data',
                    'Destination': '/var/lib/postgresql/data'}]},
                {'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': 'web'}},
                 'State': {'Running': False}, 'Mounts': [{'Type': 'volume', 'Name': project + '_media_data',
                    'Destination': '/app/media'}]}]
        self.assertEqual(prepare.cold_source_volumes(rows),
                         {'postgres_data': project + '_postgres_data', 'media_data': project + '_media_data'})
        rows[0]['State']['Running'] = True
        with self.assertRaises(ValueError):
            prepare.cold_source_volumes(rows)

    def test_pressure_cells_have_only_50_and_180_and_exact_mixed_denominator(self):
        subject = self.subject()
        rows = subject.retest_cells('final', ['selected'], rounds=2)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['count'] for row in rows}, {50, 180})
        self.assertEqual({row['round'] for row in rows}, {1, 2})
        self.assertEqual(subject.role_counts('entry-mixed', 50), {'login': 17, 'startup': 17, 'save': 16})
        self.assertEqual(subject.role_counts('entry-mixed', 180), {'login': 60, 'startup': 60, 'save': 60})
        with self.assertRaises(ValueError):
            subject.retest_cells('exploration', ['selected'], counts=(240,))

    def test_600_second_answering_receives_separate_preparation_drain_and_statistics_bounds(self):
        subject = self.subject()
        bounds = subject.phase_bounds('paper-20q')
        self.assertEqual(bounds['answer_seconds'], 600)
        self.assertEqual(bounds['preparation_seconds'], 300)
        self.assertEqual(bounds['acceptance_seconds'], 60)
        self.assertEqual(bounds['processing_seconds'], 300)
        self.assertEqual(bounds['statistics_seconds'], 300)
        self.assertEqual(bounds['observer_seconds'], 1630)

    def test_candidate_growth_has_no_numeric_ceiling_and_keeps_required_relations(self):
        subject = self.subject()
        config = subject.r0_parameters()
        config.update(ASSESSMENT_REMAINING_WORKERS='128')
        next_values = subject.grow_candidate(config, 'assessment-workers')
        self.assertEqual(next_values['ASSESSMENT_REMAINING_WORKERS'], '160')
        config.update(ASSESSMENT_ENTRY_LOGIN_LIMIT='14')
        next_values = subject.grow_candidate(config, 'login-limit')
        self.assertGreater(int(next_values['ASSESSMENT_ENTRY_HEAVY_LIMIT']), 14)
        self.assertGreater(int(next_values['ASSESSMENT_ENTRY_WORKERS']), int(next_values['ASSESSMENT_ENTRY_HEAVY_LIMIT']))
        budget = subject.connection_budget(subject.grow_candidate(config, 'submission'))
        self.assertLessEqual(budget['planned'], budget['available'])

    def test_formal_acceptance_requires_every_count_mode_round_and_browser_record(self):
        subject = self.subject()
        rows = [{**cell, 'passed': True, 'browser_passed': True, 'generator_valid': True,
                 'monitor_ok': True, 'evidence_identity': {'workload_profile': 'full-flow-20q-gradual-login-v1', 'application': 'r5', 'configuration': 'one'}}
                for cell in subject.retest_cells('final', ['selected'], rounds=2)]
        self.assertTrue(subject.formal_passed(rows))
        self.assertFalse(subject.formal_passed(rows[:-1]))
        rows[0]['browser_passed'] = False
        self.assertFalse(subject.formal_passed(rows))

    def test_tuning_requires_observed_bottleneck_and_never_tunes_a_generator_failure(self):
        self.assertIsNotNone(importlib.util.find_spec('run_student_capacity_retest'), 'Remote controller missing')
        import run_student_capacity_retest as controller
        row = {'generator_valid': True, 'stats_ready': True, 'performance_passed': False,
               'timings': {'save_scheduled': {'p95_ms': 4000}},
               'diagnostics': {'cpu_busy': .45, 'assessment_queue_p95_ms': 3000, 'pool_waiting_max': 0}}
        self.assertEqual(controller.bottleneck_dimension(row), 'assessment-workers')
        self.assertEqual(controller.bottleneck_dimension(row | {'diagnostics': {'cpu_busy': .45, 'assessment_pool_waiting_max': 4}}),
                         'assessment-pool')
        self.assertIsNone(controller.bottleneck_dimension(row | {'diagnostics': {'cpu_busy': .45, 'pool_waiting_max': 4,
                            'pool_waiting_by_alias': {'kt_background': 4, 'kt_assessment': 0}}}))
        self.assertIsNone(controller.bottleneck_dimension(row | {'generator_valid': False}))
        self.assertIsNone(controller.bottleneck_dimension(row | {'diagnostics': {}}))

    def test_memory_guard_scales_to_fifteen_percent_of_host_and_requires_three_spaced_samples(self):
        self.assertIsNotNone(importlib.util.find_spec('run_student_capacity_retest'), 'Remote controller missing')
        import run_student_capacity_retest as controller
        guard = controller.RetestSafetyWindow(16 * 1024**3)
        self.assertIsNone(guard.sample(0, 2300 * 1024**2))
        self.assertIsNone(guard.sample(15, 2300 * 1024**2))
        self.assertEqual(guard.sample(30, 2300 * 1024**2), 'available_memory_below_host_floor_three_15s_samples')

    def test_diagnostics_use_actual_observer_counter_and_pool_shapes(self):
        import run_student_capacity_retest as controller
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'streams': []}))
            samples = [{'host_cpu_counters': {'user_ticks': 100, 'idle_ticks': 100, 'iowait_ticks': 0},
                        'pgbouncer': {'kt_assessment': {'cl_waiting': 2}}},
                       {'host_cpu_counters': {'user_ticks': 150, 'idle_ticks': 150, 'iowait_ticks': 0},
                        'pgbouncer': {'kt_assessment': {'cl_waiting': 3}}}]
            (directory / 'observation.samples.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in samples))
            result = controller.collect_diagnostics(directory, manifest, 0, 100)
            self.assertEqual(result['cpu_busy'], .5)
            self.assertEqual(result['pool_waiting_max'], 3)

    def test_a_failed_formal_cell_returns_to_tuning_before_new_independent_rounds(self):
        import run_student_capacity_retest as controller
        subject = self.subject()
        probe = object.__new__(controller.RetestMatrix)
        probe.configs, probe.selections, probe.report = {}, [], {}
        probe.save_report = Mock()
        probe.publish_controller = Mock()
        probe.collector = Mock()
        probe.collector_log = Mock()
        probe.stop_collector = Mock()
        stages = []
        final_calls = 0
        with tempfile.TemporaryDirectory() as temporary:
            probe.root = Path(temporary)
            (probe.root / 'private').mkdir()
            config = {'services': {'entry': {'environment': subject.r0_parameters()}}}
            (probe.root / 'private/compose-R0.json').write_text(json.dumps(config))
            probe.collector_stop = probe.root / 'collector.stop'
            def register(name, config):
                probe.configs[name] = (probe.root / (name + '.json'), config)
            probe.register = register
            def stage(stage, names, modes=subject.MODES, **kwargs):
                nonlocal final_calls
                stages.append(stage)
                rows = [{**row, 'passed': True, 'browser_passed': True, 'generator_valid': True,
                         'monitor_ok': True, 'integrity_passed': True, 'full_denominator_completed': True,
                         'evidence_identity': {'workload_profile': 'full-flow-20q-gradual-login-v1', 'application': 'r5', 'configuration': names[0]},
                         'stats_ready': True, 'safety_stop': False, 'timings': {}}
                        for row in subject.retest_cells(stage, names, modes, counts=kwargs.get('counts', (50, 180)),
                                                       rounds=kwargs.get('rounds', 1))]
                if stage == 'final':
                    final_calls += 1
                    if final_calls == 1:
                        failed = next(row for row in rows if row['mode'] == 'full-flow' and row['count'] == 180)
                        failed.update(passed=False, timings={'save_scheduled': {'planned': 180, 'p95_ms': 4000}},
                            diagnostics={'cpu_busy': .4, 'assessment_queue_p95_ms': 3000, 'pool_waiting_max': 0})
                        return [failed]
                return rows
            probe.stage_v3 = stage
            probe.run()
            self.assertTrue(probe.report['formal_capacity_accepted'])
            self.assertEqual(final_calls, 2)
            self.assertIn('tuning', stages[stages.index('final') + 1:])

    def test_worker_preflight_counts_real_children_instead_of_only_environment(self):
        import run_student_capacity_retest as controller
        self.assertTrue(callable(getattr(controller, 'http_worker_inventory', None)), 'Actual worker preflight missing')
        config = {'services': {'entry': {'environment': {'GUNICORN_WORKERS': '2'}}}}
        item = {'Id': 'entry-container', 'State': {'Pid': 11},
                'Config': {'Labels': {'com.docker.compose.service': 'entry'}}}
        with tempfile.TemporaryDirectory() as raw:
            proc = Path(raw)
            (proc / '11/task/11').mkdir(parents=True)
            (proc / '11/cmdline').write_bytes(b'gunicorn: master')
            (proc / '11/task/11/children').write_text('12')
            (proc / '12').mkdir()
            (proc / '12/cmdline').write_bytes(b'gunicorn: worker')
            with self.assertRaisesRegex(ValueError, 'worker'):
                controller.http_worker_inventory([item], config, proc)
            (proc / '13').mkdir()
            (proc / '13/cmdline').write_bytes(b'gunicorn: worker')
            (proc / '11/task/11/children').write_text('12 13')
            self.assertEqual(controller.http_worker_inventory([item], config, proc)[0]['workers'], 2)

    def test_growing_workers_adjusts_observed_memory_headroom_before_hitting_old_container_limit(self):
        import run_student_capacity_retest as controller
        self.assertTrue(callable(getattr(controller, 'resource_headroom', None)), 'Resource headroom adjustment missing')
        config = {'services': {'assessment': {'mem_limit': 1024, 'environment': {'GUNICORN_WORKERS': '4'}}}}
        changes = {'ASSESSMENT_REMAINING_WORKERS': '8'}
        diagnostics = {'role_working_set_bytes': {'assessment': 800}, 'memory_available_min_bytes': 8 * 1024**3,
                       'memory_total_bytes': 16 * 1024**3}
        result = controller.resource_headroom(config, changes, diagnostics)
        self.assertGreater(result['resource.assessment.mem_limit'], 1600)
        with self.assertRaisesRegex(ValueError, 'memory'):
            controller.resource_headroom(config, changes, diagnostics | {'memory_available_min_bytes': 10})


if __name__ == '__main__':
    unittest.main()
