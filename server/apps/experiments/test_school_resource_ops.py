import importlib.util
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class SchoolPostgresResourceTests(SimpleTestCase):
    def module(self):
        path = Path(settings.BASE_DIR) / 'scripts/ops/school_pg_capacity_limits.py'
        self.assertTrue(path.exists(), 'Independent PG resource limiter missing')
        spec = importlib.util.spec_from_file_location('pg_resource_limit', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_actual_kernel_limits_restore_original_unlimited_values(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            group = Path(temporary)
            originals = {'memory.max': 'max', 'memory.swap.max': 'max', 'cpu.max': 'max 100000', 'cpu.weight': '100'}
            for name, value in originals.items():
                (group / name).write_text(value)
            (group / 'memory.current').write_text('1048576')
            (group / 'memory.stat').write_text('inactive_file 0\nfile 0\nfile_dirty 0\nfile_writeback 0\n')
            before = module.apply_limits(group)
            self.assertEqual(before, originals)
            self.assertEqual(module.read_limits(group), module.TARGET)
            module.restore_limits(group, before)
            self.assertEqual(module.read_limits(group), originals)

    def test_refuses_to_reduce_memory_without_one_gib_headroom(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            group = Path(temporary)
            for name in module.TARGET:
                (group / name).write_text('max' if name != 'cpu.max' else 'max 100000')
            (group / 'memory.current').write_text(str(4 * 1024**3))
            (group / 'memory.stat').write_text('inactive_file 0\nfile 0\nfile_dirty 0\nfile_writeback 0\n')
            with self.assertRaisesRegex(ValueError, 'headroom'):
                module.apply_limits(group)
            self.assertEqual((group / 'memory.max').read_text(), 'max')

    def test_failure_stops_test_workload_before_uncapping_postgres(self):
        module = self.module()
        order = []
        with patch.object(module, 'stop_test_workload', side_effect=lambda: order.append('stop')):
            with patch.object(module, 'restore_limits', side_effect=lambda *args: order.append('restore')):
                module.restore_after_failure(Path('/unused'), {}, {})
        self.assertEqual(order, ['stop', 'restore'])

    def test_failed_workload_stop_keeps_postgres_capped(self):
        module = self.module()
        with patch.object(module, 'stop_test_workload', side_effect=RuntimeError('stop failed')):
            with patch.object(module, 'restore_limits') as restore:
                with self.assertRaises(RuntimeError):
                    module.restore_after_failure(Path('/unused'), {}, {})
                restore.assert_not_called()

    def test_clean_inactive_cache_is_discounted_but_dirty_pages_are_not(self):
        module = self.module()
        values = {'anon': 28225536, 'file': 4653166592, 'inactive_file': 4504723456,
                  'kernel': 148099072, 'file_dirty': 0, 'file_writeback': 0}
        self.assertEqual(module.nonreclaimable_memory(4829491200, values), 324767744)
        self.assertEqual(module.nonreclaimable_memory(4829491200,
            {**values, 'file_dirty': 1024, 'file_writeback': 2048}), 324770816)

    def test_high_anonymous_memory_cannot_pass_by_claiming_file_cache(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            group = Path(temporary)
            for name in module.TARGET:
                (group / name).write_text('max' if name != 'cpu.max' else 'max 100000')
            (group / 'memory.current').write_text(str(5 * 1024**3))
            (group / 'memory.stat').write_text('anon 4294967296\nfile 1073741824\ninactive_file 1073741824\nfile_dirty 0\nfile_writeback 0\n')
            with self.assertRaisesRegex(ValueError, 'headroom'):
                module.apply_limits(group)
            self.assertEqual((group / 'memory.max').read_text(), 'max')

    def test_cleanup_marks_stale_running_candidate_failed_without_overwriting_pass(self):
        module = self.module()
        state = {'run_id': module.RUN_ID, 'state': 'running', 'stage': 'baseline-history-http'}
        value = module.failed_candidate_status(state, {'error_type': 'CalledProcessError'})
        self.assertEqual(value['state'], 'failed')
        self.assertEqual(value['stage'], 'baseline-history-http')
        self.assertEqual(value['failure_detected_by'], 'independent-postgres-resource-helper')
        self.assertIsNone(module.failed_candidate_status({**state, 'run_id': 'other'}, {}))
        self.assertIsNone(module.failed_candidate_status({**state, 'state': 'passed'}, {}))

    def test_v7_phase_policy_is_source_bound_and_only_approved_postgres_budget(self):
        module = self.module()
        policy = {'run_id': module.RUN_ID, 'source_sha256': 'a' * 64, 'phase': 'short-50',
                  'postgres_memory_gib': 4, 'postgres_cpus': 5}
        expected = {'memory.max': str(4 * 1024**3), 'memory.swap.max': '0', 'cpu.max': '500000 100000', 'cpu.weight': '120'}
        self.assertEqual(module.phase_target(policy, 'a' * 64), expected)
        for key, value in (('source_sha256', 'b' * 64), ('postgres_cpus', 6), ('postgres_memory_gib', 6), ('phase', 'arbitrary')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.phase_target({**policy, key: value}, 'a' * 64)

    def test_stale_finished_child_does_not_skip_stopping_verified_bootstrap(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            child = Path(temporary) / 'active-process.pid'
            child.write_text('42')
            with patch.object(module.os, 'getpgid', side_effect=ProcessLookupError, create=True):
                with patch.object(module.os, 'kill') as kill:
                    module.terminate_owned_pipeline(99, child)
                    kill.assert_called_once_with(99, module.signal.SIGTERM)

    def test_short_completion_checks_both_fixed_sizes_and_restores_without_formal_claim(self):
        module = self.module()
        config = {'source_sha256': 'a' * 64, 'postgres_memory_gib': 4, 'postgres_cpus': 5, 'dynamic_cpu_control': False}
        rows = {str(n): [{'student_count': n, 'checks': {'actual_capture': True}, 'passed': True,
                         'source_config': config, 'started': 10, 'ended': 400} for _ in range(2)] for n in (50, 300)}
        observation = {'oom_kills': 0, 'swap_start_bytes': 0, 'swap_end_bytes': 0,
                       'started_at': 5, 'ended_at': 401, 'memory_cpu_samples': [1]}
        self.assertTrue(module.short_evidence_valid(rows, observation, 'a' * 64))
        rows['300'][1]['checks']['actual_capture'] = False
        self.assertFalse(module.short_evidence_valid(rows, observation, 'a' * 64))
