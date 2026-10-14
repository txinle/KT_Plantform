"""Worker selection is a bounded diagnostic, never formal capacity acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))


class WorkerShortTests(unittest.TestCase):
    def module(self):
        import school_worker_short
        return school_worker_short

    def policy(self, workers=8):
        return {'version': 'worker-short-20261014-v1', 'workers': workers,
                'assessment_pool_size': 8, 'duration_seconds': 360,
                'entry_deadline_seconds': 120, 'observer_interval_seconds': 2,
                'minimum_counts_by_item': {'0': 150, '1': 120}}

    def test_only_frozen_worker_and_resource_comparison_is_allowed(self):
        module = self.module()
        for workers in (8, 12):
            self.assertEqual(module.validate_policy(self.policy(workers))['workers'], workers)
        for change in ({'workers': 24}, {'duration_seconds': 600},
                       {'assessment_pool_size': 24}, {'entry_deadline_seconds': 200}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.validate_policy({**self.policy(), **change})

    def test_ready_requires_matching_fixture_and_both_real_browser_contexts(self):
        module = self.module()
        binding = {'fixture_sha256': 'a' * 64, 'plan_sha256': 'b' * 64}
        protocol = {'pid': 1, 'protocol_count': 178, **binding}
        browser = {'pid': 2, 'probe_count': 2, 'fixture_binding': binding, **binding}
        self.assertTrue(module.armed_matches(protocol, browser, binding, 178))
        for change in ({'probe_count': 1}, {'fixture_sha256': 'c' * 64}, {'fixture_binding': {}}):
            self.assertFalse(module.armed_matches(protocol, {**browser, **change}, binding, 178))

    def test_fixed_stop_and_entry_failure_do_not_use_improvement_samples(self):
        module = self.module()
        self.assertIsNone(module.short_stop_reason(self.policy(), 0, 119, 178, 1, False))
        self.assertEqual(module.short_stop_reason(self.policy(), 0, 120, 178, 1, False),
                         'short_all_180_entry_deadline')
        self.assertIsNone(module.short_stop_reason(self.policy(), 0, 359, 178, 2, True))
        self.assertEqual(module.short_stop_reason(self.policy(), 0, 360, 178, 2, True),
                         'short_fixed_window_complete')

    def test_paired_quantiles_require_same_students_and_all_gates(self):
        module = self.module()
        def rows(seconds):
            return [{'student_index': student, 'item_index': item, 'successful': True,
                     'metric': 'submit_next_ready', 'elapsed_seconds': seconds}
                    for item in (0, 1) for student in range(178)]
        base = {'eligible': True, 'confirmed_submissions': 356,
                'secondary_p95': {'login': 1, 'save': 1, 'events': 1}}
        before = {**base, 'operations': rows(5)}
        after = {**base, 'operations': rows(4)}
        result = module.compare_runs(before, after)
        self.assertTrue(result['candidate_qualified'])
        after['confirmed_submissions'] = 355
        self.assertFalse(module.compare_runs(before, after)['candidate_qualified'])
        after['confirmed_submissions'] = 356
        after['operations'].pop()
        self.assertFalse(module.compare_runs(before, after)['candidate_qualified'])
        after['eligible'] = False
        self.assertFalse(module.compare_runs(before, after)['candidate_qualified'])

    def test_missing_secondary_measurements_cannot_qualify_candidate(self):
        module = self.module()
        base = {'eligible': True, 'confirmed_submissions': 1, 'operations': [],
                'secondary_p95': {'login': None, 'save': None, 'events': None}}
        result = module.compare_runs(base, copy.deepcopy(base))
        self.assertFalse(result['candidate_qualified'])

    def test_two_browser_connectivity_has_no_protocol_workflow_to_complete(self):
        module = self.module()
        self.assertFalse(module.protocol_finished_error({'workflow_completed': False, 'errors': []}, 0))
        self.assertTrue(module.protocol_finished_error({'workflow_completed': False, 'errors': []}, 178))
        self.assertTrue(module.protocol_finished_error({'workflow_completed': False, 'errors': ['failed']}, 0))

    def test_short_source_accepts_only_bound_8_or_12_and_keeps_default_24(self):
        from school_diagnostic_runtime import DiagnosticRuntime
        from school_transition_runtime import validate_transition_source, SafetyInterrupted
        from test_school_transition_runtime import SixPoolProfileTests
        for workers in (8, 12):
            source = SixPoolProfileTests().source()
            source['worker_short_profile'] = self.policy(workers)
            source['roles']['assessment']['workers'] = workers
            validate_transition_source(source)
            runtime = DiagnosticRuntime.__new__(DiagnosticRuntime)
            runtime.config = {'source_config': source}
            self.assertEqual(runtime.http_workers['assessment'], workers)
            source['roles']['assessment']['memory_bytes'] = 2 * 1024**3
            with self.assertRaises(SafetyInterrupted):
                validate_transition_source(source)

    def test_coordinator_never_releases_start_for_failed_browser(self):
        from unittest.mock import Mock
        from school_transition_runtime import TransitionRuntime, SafetyInterrupted
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt, bridge = root/'attempt', root/'bridge'
            attempt.mkdir(); bridge.mkdir()
            (bridge/'browser-launch-error.json').write_text('{"error":"fixture_missing"}')
            runtime = TransitionRuntime.__new__(TransitionRuntime)
            with self.assertRaises(SafetyInterrupted):
                runtime.release_armed_start(attempt, bridge, {'protocol_usernames': ['u']}, Mock())
            self.assertFalse((bridge/'start.json').exists())

    def test_browser_final_report_arriving_after_stop_is_collected(self):
        from unittest.mock import patch
        from school_transition_runtime import TransitionRuntime
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt, bridge = root/'attempt', root/'bridge'
            attempt.mkdir(); bridge.mkdir()
            def deliver(_):
                (bridge/'browser.json').write_text('{"probes":[{"submitted_count":1},{"submitted_count":1}]}')
            runtime = TransitionRuntime.__new__(TransitionRuntime)
            with patch('school_transition_runtime.time.sleep', side_effect=deliver):
                self.assertTrue(runtime.collect_browser_reports(attempt, bridge, wait_for_final=True))
            self.assertEqual(len(json.loads((attempt/'browser.json').read_text())['probes']), 2)

    def test_protocol_waits_without_http_then_accepts_bound_start(self):
        from unittest.mock import patch
        from school_natural_load import await_armed_start
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, plan = root/'fixture.json', root/'plan.json'
            manifest.write_text('{}'); plan.write_text('{}')
            binding = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for name, path in [('fixture_sha256', manifest), ('plan_sha256', plan)]}
            start = root/'start.json'
            def release(_):
                self.assertTrue((root/'armed.json').exists())
                start.write_text(json.dumps({'start_at': 120, 'fixture_binding': binding}))
            with patch('school_natural_load.time.time', return_value=100), \
                 patch('school_natural_load.time.sleep', side_effect=release):
                result = await_armed_start(manifest, plan, root/'armed.json', start, None, 178)
            self.assertEqual(result, 120)
            self.assertEqual(json.loads((root/'armed.json').read_text())['protocol_count'], 178)


if __name__ == '__main__':
    unittest.main()
