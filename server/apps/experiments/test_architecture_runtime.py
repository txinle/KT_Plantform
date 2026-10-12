import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


class ArchitectureRuntimeTests(unittest.TestCase):
    def module(self):
        directory = Path(__file__).resolve().parents[2] / 'scripts/ops'
        sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location('school_architecture_runtime', directory / 'school_architecture_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generator_command_is_bounded_owned_and_uses_one_fixed_workload(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            runtime = module.ArchitectureRuntime({'candidate_dir': str(Path(directory) / 'deploy'),
                'private_dir': directory, 'compose_overlay_path': str(Path(directory) / 'images.json'),
                'source_config': {}, 'images': {role: 'sha256:' + 'a' * 64 for role in module.ROLES}})
            command = runtime.load_command(Path(directory), 'diagnostic-180', ['scripts/load/school_architecture_load.py'])
            self.assertEqual(command[command.index('--cpus') + 1], '2')
            self.assertEqual(command[command.index('--memory') + 1], '1g')
            self.assertIn('com.docker.compose.project=kt-school-e2e', command)
            self.assertNotIn('kt_platform', ' '.join(command))
            self.assertEqual(runtime.observer_script, Path(directory) / 'server/scripts/load/observe_school_runtime.py')

    def test_stage_names_cannot_escape_private_evidence_directory(self):
        module = self.module()
        for stage in ('../production', '/absolute', 'formal-300', 'diagnostic-300', 'smoke-180'):
            with self.assertRaises(ValueError):
                module.stage_profile(stage)
        self.assertEqual(module.stage_profile('diagnostic-50'), ('diagnostic', 50))
        self.assertEqual(module.stage_profile('formal-2'), ('formal', 180))

    def test_incomplete_workflow_is_distinct_from_corrupt_known_facts(self):
        module = self.module()
        incomplete = {'passed': False, 'known_fact_integrity_passed': True,
            'known_fact_integrity_checks': {
                'all_acknowledged_events_persisted_and_material_matched': True,
                'all_acknowledged_checkpoints_persisted_and_original_fields_matched': True},
            'known_navigation_integrity_passed': True}
        self.assertIsNone(module.confirmed_fact_failure(incomplete))
        for key in ('all_acknowledged_events_persisted_and_material_matched',
                    'all_acknowledged_checkpoints_persisted_and_original_fields_matched',
                    'present_unacknowledged_events_material_matched',
                    'zero_unexpected_or_duplicate_client_events'):
            damaged = {**incomplete, 'known_fact_integrity_checks': {
                **incomplete['known_fact_integrity_checks'], key: False}}
            self.assertEqual(module.confirmed_fact_failure(damaged), key)
        self.assertEqual(module.confirmed_fact_failure({**incomplete,
            'known_navigation_integrity_passed': False}), 'known_navigation_integrity_passed')
        self.assertIsNone(module.confirmed_fact_failure({}))

    def test_protocol_only_acceptance_never_claims_browser_experience(self):
        module = self.module()
        checks = dict.fromkeys(('load_completed', 'operation_targets_passed', 'data_integrity_passed',
            'grading_coverage_export_consistent', 'background_drained', 'observer_completed',
            'no_oom', 'observer_covers_load'), True)
        result = module.acceptance_fields('protocol_only', 'formal-1', checks)
        self.assertTrue(result['formal_protocol_capacity_accepted'])
        self.assertFalse(result['formal_capacity_accepted'])
        self.assertEqual(result['browser_acceptance'], 'not_requested_by_user')
        for stage, evidence in [('diagnostic-180', checks), ('smoke-10', checks), ('formal-1', {}),
                                ('formal-2', {**checks, 'data_integrity_passed': False})]:
            self.assertFalse(module.acceptance_fields('protocol_only', stage, evidence)['formal_protocol_capacity_accepted'])
        self.assertFalse(module.acceptance_fields('protocol_and_browser', 'formal-1', checks)['formal_capacity_accepted'])
        with self.assertRaises(ValueError):
            module.acceptance_fields('skip_integrity', 'formal-1', checks)
