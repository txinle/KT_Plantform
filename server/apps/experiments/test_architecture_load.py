import importlib.util
import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch


LOAD = Path(__file__).resolve().parents[2] / 'scripts/load'


class ArchitectureLoadTests(unittest.TestCase):
    def test_only_explicit_raw_fact_damage_triggers_architecture_safety_stop(self):
        sys.path.insert(0, str(LOAD.parent / 'ops'))
        from school_architecture_runtime import confirmed_fact_failure
        self.assertEqual(confirmed_fact_failure({'raw_fact_integrity_failed': True}), 'raw_fact_integrity_failed')
        self.assertIsNone(confirmed_fact_failure({'passed': False, 'status': 'read_failed'}))

    def test_stage_requires_grading_coverage_export_reconciliation(self):
        ops = LOAD.parent / 'ops'
        sys.path.insert(0, str(ops))
        import school_architecture_runtime as runtime
        subject = object.__new__(runtime.ArchitectureRuntime)
        subject.candidate = Path('/candidate/deploy')
        subject.config = {'source_config': {}, 'images': {}}
        subject.native_probe = None
        subject.children = []
        subject.compose = []
        for name in ('verify_runtime', 'bind_attempt', 'backlog', 'load_command', 'direct_command', 'source_value'):
            setattr(subject, name, Mock(return_value={} if name in ('backlog', 'source_value') else []))
        subject.monitored_process = Mock(return_value=0)
        subject.drain = Mock(return_value={'passed': True})
        subject.run_private = Mock(return_value=0)
        process = Mock(returncode=0)
        process.poll.return_value = None
        evidence = {
            'gateway-routing.json': {'passed': True, 'requests': [
                {'request_id': str(index) * 32, 'expected_role': role, 'observed_roles': [role]}
                for index, role in enumerate(('web', 'student-read', 'teacher-read'), start=1)]},
            'http.json': {'workflow_completed': True, 'performance_passed': True, 'started': 2, 'ended': 3},
            'persistence.json': {'passed': True},
            'architecture-results.json': {'passed': False, 'status': 'mismatch'},
            'observation.json': {'state': 'completed', 'oom_kills': 0, 'started_at': 1, 'ended_at': 4},
        }
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runtime, 'read', side_effect=lambda path: {'task_id': 1} if path.name == 'fixture.json' else {'started_at': 1}), \
                patch.object(runtime, 'read_evidence', side_effect=lambda path: (evidence[path.name], None)), \
                patch.object(runtime, 'write_once'), patch.object(runtime.time, 'sleep'), \
                patch.object(runtime.subprocess, 'Popen', return_value=process):
            result = subject.run_stage('smoke-3', Path(directory), {'mode': 'smoke', 'hard_timeout_seconds': 1800})
            self.assertFalse(result['protocol_passed'])
            self.assertFalse(result['checks']['grading_coverage_export_consistent'])
            self.assertTrue(any('verify_school_architecture_results' in call.args[1] for call in subject.direct_command.call_args_list))
            subject.direct_command.reset_mock()
            evidence['persistence.json']['known_fact_integrity_checks'] = {'all_confirmed_entries_match_original_material': False}
            damaged = Path(directory) / 'damaged'
            damaged.mkdir()
            with self.assertRaises(runtime.SafetyInterrupted):
                subject.run_stage('smoke-3', damaged, {'mode': 'smoke', 'hard_timeout_seconds': 1800})
            self.assertFalse(any('verify_school_architecture_results' in call.args[1] for call in subject.direct_command.call_args_list))

    def module(self):
        sys.path.insert(0, str(LOAD))
        spec = importlib.util.spec_from_file_location('school_architecture_load', LOAD / 'school_architecture_load.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, count):
        return {'database': 'kt_e2e', 'capture_protocol': 'school-capture-v2-protocol-v1',
                'student_count': count, 'item_count': 20, 'usernames': [f'fixture-{i}' for i in range(count)],
                'source_config': {'source_sha256': 'a' * 64, 'frontend_sha256': 'b' * 64, 'config_sha256': 'c' * 64,
                                  'shell_url': '/static/spa-assets/capture-shell-' + 'a' * 20 + '.html'}}

    def test_diagnostics_use_fixed_counts_and_short_budget_without_formal_claim(self):
        module = self.module()
        for count in (1, 3, 10, 50, 180):
            profile = module.validate_profile(self.fixture(count), 'diagnostic')
            self.assertEqual(profile['internal_budget_seconds'], 240)
            self.assertEqual(profile['hard_timeout_seconds'], 300)
            self.assertFalse(profile['formal_capacity_accepted'])
        for count in (0, 150, 300):
            with self.assertRaises(ValueError):
                module.validate_profile(self.fixture(count), 'diagnostic')

    def test_formal_and_smoke_profiles_preserve_full_load_budget_and_denomination(self):
        module = self.module()
        self.assertEqual(module.validate_profile(self.fixture(180), 'formal')['internal_budget_seconds'], 1750)
        for count in (3, 10):
            self.assertEqual(module.validate_profile(self.fixture(count), 'smoke')['hard_timeout_seconds'], 1800)
            with self.assertRaises(ValueError):
                module.validate_profile(self.fixture(count), 'formal')

    def test_wrong_database_duplicate_accounts_or_unbound_source_is_rejected(self):
        module = self.module()
        for change in ({'database': 'kt_platform'}, {'usernames': ['same'] * 180}, {'source_config': {}}):
            with self.assertRaises(ValueError):
                module.validate_profile({**self.fixture(180), **change}, 'formal')

    def test_browser_workload_requires_the_frozen_build_shell(self):
        for value in ('', '/student/sessions/1/questions/1/', 'https://external.example/shell.html'):
            fixture = self.fixture(3)
            fixture['source_config']['shell_url'] = value
            with self.assertRaises(ValueError):
                self.module().validate_profile(fixture, 'smoke')

    def test_diagnostic_summary_uses_original_count_even_when_no_one_authenticated(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35,
            workload_version=module.BROWSER_ALIGNED_WORKLOAD, internal_budget_seconds=240)
        runner.execution_completed = False
        states = [module.StudentState(f'fixture-{i}', 'fixture-password') for i in range(3)]
        result = module.build_result(runner, states, 1, 2, expected_students=3)
        self.assertEqual(result['student_count'], 3)
        self.assertEqual(result['synchronized_windows']['login_post']['expected_student_count'], 3)
        self.assertFalse(result['performance_passed'])
