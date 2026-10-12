import importlib.util
import json
import tempfile
from unittest.mock import patch
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TeacherScopeMonitorTests(SimpleTestCase):
    def module(self):
        path = Path(settings.BASE_DIR) / 'scripts/ops/school_teacher_scope_monitor.py'
        self.assertTrue(path.exists(), 'Independent scope monitor missing')
        spec = importlib.util.spec_from_file_location('scope_monitor', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_actual_scope_post_then_preview_get_records_combined_elapsed(self):
        module = self.module()
        calls = []
        class Client:
            def request(self, state, path, **kwargs):
                calls.append((path, kwargs))
                return 200, {'current': {'experiment_ids': [7, 8], 'class_id': 9}}, 99
        ticks = iter([10, 10.7])
        result = module.measure_switch(Client(), object(), '/api/v1/teacher/courses/1/data/',
            {'experiment_ids': [7, 8], 'class_id': 9}, clock=lambda: next(ticks))
        self.assertAlmostEqual(result, .7)
        self.assertEqual(calls[0][1]['method'], 'POST')
        self.assertTrue(calls[0][0].endswith('/scope/'))
        self.assertIn('experiment_ids=7%2C8', calls[1][0])
        self.assertIn('table=sessions&page=1', calls[1][0])

    def test_report_uses_only_complete_in_window_samples_and_no_identities(self):
        module = self.module()
        samples = [{'teacher_slot': teacher, 'scope_slot': scope, 'started_at': 20,
                    'ended_at': 20.7, 'seconds': .7, 'ok': True}
                   for teacher in range(5) for scope in range(5)]
        samples += [{'teacher_slot': 0, 'scope_slot': 0, 'started_at': 9,
                     'ended_at': 10.5, 'seconds': 1.5, 'ok': False}]
        result = module.evaluate(samples, {'started_at': 10, 'ended_at': 30}, True)
        self.assertTrue(result['passed'])
        self.assertEqual(result['sample_count'], 25)
        self.assertNotIn('username', json.dumps(result))
        self.assertNotIn('password', json.dumps(result))
        samples[0]['seconds'] = 2
        samples[1]['seconds'] = 2
        self.assertFalse(module.evaluate(samples, {'started_at': 10, 'ended_at': 30}, True)['passed'])

    def test_resource_failure_stops_monitor_even_if_candidate_status_is_stale(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            (report / 'status.json').write_text(json.dumps({'run_id': module.RUN,
                'state': 'running', 'stage': 'baseline-history-http'}))
            (report / 'postgres-resource-profile.json').write_text(json.dumps({'run_id': module.RUN,
                'state': 'failed-original-restored'}))
            with patch.object(module, 'PUBLIC', report), self.assertRaisesRegex(RuntimeError, 'resource'):
                module.state()

    def test_v7_monitor_requires_sealed_upgrade_binding_to_same_retained_fixtures(self):
        module = self.module()
        binding = {'run_id': module.RUN, 'candidate_archive_sha256': 'a' * 64}
        sealed = {'run_id': module.RUN, 'candidate_archive_sha256': 'a' * 64,
            'old_source_sha256': module.SHA, 'archive': '/home/ktdata/school-candidate-v7.tar',
            'fixture_run_id': module.RUN + '-v6'}
        self.assertEqual(module.bound_source(binding, sealed), 'a' * 64)
        with self.assertRaises(RuntimeError):
            module.bound_source(binding, {**sealed, 'fixture_run_id': module.RUN + '-v7'})
