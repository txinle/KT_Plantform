import copy
import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/load'))


class SchoolDiagnosticSweepTests(unittest.TestCase):
    def module(self):
        path = ROOT / 'scripts/ops/school_diagnostic_sweep.py'
        self.assertTrue(path.exists(), 'diagnostic sweep implementation missing')
        spec = importlib.util.spec_from_file_location('school_diagnostic_sweep', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def manifest(self):
        return {'run_id': 'isolated-diagnostic', 'database': 'kt_e2e',
                'binding': {key: 'a' * 64 for key in ('source_sha256', 'frontend_sha256', 'config_sha256')},
                'stages': [{'name': name, 'student_count': count, 'duration_seconds': duration,
                            'timeout_seconds': 1800 if duration == 0 else 2700,
                            'fixture_id': name, 'cwd': str(ROOT),
                            'commands': {step: ['python', step, '{attempt_dir}', '{fixture_id}']
                                         for step in ('setup', 'readiness', 'load', 'collect', 'reconcile')}}
                           for name, count, duration in [('short-3', 3, 0), ('short-50', 50, 0),
                               ('short-150', 150, 0), ('short-300', 300, 0),
                               ('baseline-300', 300, 1800), ('mixed-300', 300, 1800)]]}

    def successful(self, stage, step, argv, attempt_dir, timeout):
        return {'returncode': 0, 'performance_passed': True, 'data_integrity_passed': True,
                'workflow_completed': True}

    def test_failed_fifty_does_not_prevent_other_four_stages(self):
        module = self.module()
        seen = []
        def execute(stage, step, argv, attempt_dir, timeout):
            seen.append((stage['name'], step))
            result = self.successful(stage, step, argv, attempt_dir, timeout)
            if stage['name'] == 'short-50' and step == 'load':
                result.update(returncode=1, performance_passed=False)
            return result
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, execute)
            self.assertEqual([row['name'] for row in report['stages']], [s['name'] for s in self.manifest()['stages']])
            self.assertEqual(len(seen), 30)
            self.assertTrue(report['execution_completed'])
            self.assertFalse(report['performance_passed'])
            self.assertFalse(report['formal_capacity_accepted'])

    def test_data_failure_and_timeout_are_preserved_and_continue(self):
        module = self.module()
        def execute(stage, step, argv, attempt_dir, timeout):
            if stage['name'] == 'short-3' and step == 'load':
                raise subprocess.TimeoutExpired(argv, timeout)
            result = self.successful(stage, step, argv, attempt_dir, timeout)
            if stage['name'] == 'short-50' and step == 'reconcile':
                result.update(returncode=1, data_integrity_passed=False)
            return result
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, execute)
            self.assertEqual(len(report['stages']), 6)
            self.assertTrue(report['execution_completed'])
            self.assertFalse(report['data_integrity_passed'])
            self.assertTrue(report['stages'][0]['steps']['load']['timed_out'])
            self.assertFalse(report['stages'][0]['workflow_completed'])
            self.assertEqual(report['stages'][1]['steps']['reconcile']['returncode'], 1)

    def test_safety_interrupt_resume_only_pending_with_fresh_fixture(self):
        module = self.module()
        seen = []
        def execute(stage, step, argv, attempt_dir, timeout):
            seen.append((stage['name'], stage['fixture_id'], step))
            if stage['name'] == 'short-50' and step == 'load':
                return {'returncode': 75, 'safety_interrupted': True}
            return self.successful(stage, step, argv, attempt_dir, timeout)
        with tempfile.TemporaryDirectory() as directory:
            first = module.run_sweep(self.manifest(), directory, execute)
            self.assertTrue(first['safety_interrupted'])
            self.assertFalse(first['execution_completed'])
            original = {str(p): p.read_bytes() for p in Path(directory).rglob('result.json')}
            resumed = []
            def resume(stage, step, argv, attempt_dir, timeout):
                resumed.append((stage['name'], stage['fixture_id']))
                self.assertIn(str(attempt_dir), argv)
                self.assertIn(stage['fixture_id'], argv)
                return self.successful(stage, step, argv, attempt_dir, timeout)
            second = module.run_sweep(self.manifest(), directory, resume)
            self.assertTrue(second['execution_completed'])
            self.assertNotIn('short-3', [name for name, _ in resumed])
            self.assertNotEqual(next(f for n, f, _ in seen if n == 'short-50'), resumed[0][1])
            self.assertTrue(all(Path(p).read_bytes() == body for p, body in original.items()))

    def test_binding_change_and_existing_evidence_refused(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest()
            module.run_sweep(manifest, directory, self.successful)
            changed = copy.deepcopy(manifest)
            changed['binding']['source_sha256'] = 'b' * 64
            with self.assertRaisesRegex(ValueError, 'binding|manifest'):
                module.run_sweep(changed, directory, self.successful)
            executor = Mock()
            module.run_sweep(manifest, directory, executor)
            executor.assert_not_called()
            with self.assertRaises(FileExistsError):
                module.write_once(Path(directory) / 'manifest.json', manifest)

    def test_missing_performance_evidence_never_passes_on_zero_exit(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, lambda *args: {'returncode': 0})
            self.assertFalse(report['performance_passed'])
            self.assertFalse(report['data_integrity_passed'])

    def test_failed_readiness_skips_http_but_collects_and_continues(self):
        module = self.module()
        seen = []
        def execute(stage, step, argv, attempt_dir, timeout):
            seen.append((stage['name'], step))
            result = self.successful(stage, step, argv, attempt_dir, timeout)
            if stage['name'] == 'short-50' and step == 'readiness':
                result['returncode'] = 1
            return result
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, execute)
            self.assertNotIn(('short-50', 'load'), seen)
            self.assertIn(('short-50', 'reconcile'), seen)
            self.assertIn(('mixed-300', 'load'), seen)
            self.assertFalse(report['stages'][1]['workflow_completed'])
            self.assertFalse(report['execution_completed'])
            self.assertFalse(report['stages'][1]['load_executed'])
            resumed = []
            def resume(stage, step, argv, attempt_dir, timeout):
                resumed.append(stage['name'])
                return self.successful(stage, step, argv, attempt_dir, timeout)
            second = module.run_sweep(self.manifest(), directory, resume)
            self.assertEqual(set(resumed), {'short-50'})
            self.assertTrue(second['execution_completed'])

    def test_source_file_change_is_safety_interruption(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bound-source.py'
            source.write_text('fixed source')
            manifest = self.manifest()
            manifest['binding']['files'] = {str(source): hashlib.sha256(source.read_bytes()).hexdigest()}
            def execute(stage, step, argv, attempt_dir, timeout):
                source.write_text('changed source')
                return self.successful(stage, step, argv, attempt_dir, timeout)
            report = module.run_sweep(manifest, Path(directory) / 'evidence', execute)
            self.assertTrue(report['safety_interrupted'])
            self.assertEqual(len(report['stages']), 1)
            self.assertTrue(all(row.get('safety_interrupted') is True
                                for step, row in report['stages'][0]['steps'].items() if step != 'setup'))

    def test_collected_final_score_supersedes_provisional_load_score(self):
        module = self.module()
        def execute(stage, step, argv, attempt_dir, timeout):
            result = self.successful(stage, step, argv, attempt_dir, timeout)
            if step == 'load':
                result['performance_passed'] = False
            return result
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, execute)
        self.assertTrue(report['performance_passed'])

    def test_resume_rejects_result_with_different_binding(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            module.run_sweep(self.manifest(), directory, self.successful)
            result_path = Path(directory) / 'short-3/attempt-0001/result.json'
            result = json.loads(result_path.read_text())
            result['binding']['config_sha256'] = 'b' * 64
            result_path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'binding'):
                module.run_sweep(self.manifest(), directory, self.successful)

    def test_exhausted_workload_deadline_still_collects_and_reconciles(self):
        module = self.module()
        seen = []
        clock = [0]
        def execute(stage, step, argv, attempt_dir, timeout):
            seen.append((stage['name'], step, timeout))
            if step == 'load':
                clock[0] += stage['timeout_seconds'] + 1
                raise subprocess.TimeoutExpired(argv, timeout)
            return self.successful(stage, step, argv, attempt_dir, timeout)
        with tempfile.TemporaryDirectory() as directory, patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            report = module.run_sweep(self.manifest(), directory, execute)
        self.assertEqual(len([row for row in seen if row[1] == 'collect']), 6)
        self.assertEqual(len([row for row in seen if row[1] == 'reconcile']), 6)
        self.assertTrue(all(timeout == 120 for _, step, timeout in seen if step == 'collect'))
        self.assertTrue(all(timeout == 300 for _, step, timeout in seen if step == 'reconcile'))
        self.assertTrue(report['execution_completed'])
        self.assertFalse(report['workflow_completed'])

    def test_safety_interruption_attempts_cleanup_before_stopping(self):
        module = self.module()
        seen = []
        def execute(stage, step, argv, attempt_dir, timeout):
            seen.append(step)
            if step == 'load':
                raise module.SafetyInterrupted('protected production')
            return self.successful(stage, step, argv, attempt_dir, timeout)
        with tempfile.TemporaryDirectory() as directory:
            report = module.run_sweep(self.manifest(), directory, execute)
        self.assertEqual(seen, ['setup', 'readiness', 'load', 'collect', 'reconcile'])
        self.assertTrue(report['safety_interrupted'])
        self.assertFalse(report['execution_completed'])

    def test_default_executor_timeout_terminates_own_process_group(self):
        module = self.module()
        from types import SimpleNamespace
        process = Mock(pid=1234)
        process.wait.side_effect = [subprocess.TimeoutExpired(['python'], 1), None]
        kill = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(module.subprocess, 'Popen', return_value=process), \
             patch.object(module, 'os', SimpleNamespace(name='posix', chmod=module.os.chmod, killpg=kill)):
            with self.assertRaises(subprocess.TimeoutExpired):
                module.subprocess_executor({'cwd': str(ROOT)}, 'load', ['python'], Path(directory), 1)
            kill.assert_called_once_with(1234, module.signal.SIGTERM)


class SchoolStudentFailureIsolationTests(unittest.TestCase):
    def test_short_login_failure_does_not_skip_successful_students(self):
        import school_short_storm as module
        runner = module.ShortRunner('http://localhost', 1, 35)
        states = [module.StudentState(str(i), 'private') for i in range(3)]
        seen = []
        def login(state):
            if state is states[0]:
                raise RuntimeError('HTTP 503')
        runner.login = login
        runner.start = lambda state: seen.append(('start', state.username))
        runner.resume = lambda state: seen.append(('resume', state.username))
        runner.answer = lambda state: seen.append(('answer', state.username))
        module.run_phases(runner, states)
        self.assertEqual(sorted(seen), [(phase, name) for phase in ('answer', 'resume', 'start') for name in ('1', '2')])
        self.assertEqual(runner.errors[0]['http_status'], 503)

    def test_mixed_has_same_two_attempt_transport_records(self):
        import school_mixed_300 as module
        runner = module.MixedRunner('http://localhost', 1, 35, course_id=1, experiment_id=2, question_id=3)
        state = module.StudentState('private', 'private')
        response = Mock(status=200, headers={'Content-Type': 'application/json', 'X-Request-ID': 'a' * 32})
        response.read.return_value = json.dumps({'data': {}, 'meta': {'request_id': 'a' * 32}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        state.opener = Mock()
        state.opener.open.side_effect = [TimeoutError(), response]
        self.assertEqual(runner.request(state, '/api/v1/auth/csrf/')[0], 200)
        self.assertEqual([r['attempt'] for r in runner.request_attempts], [1, 2])
        self.assertEqual(len({r['operation_id'] for r in runner.request_attempts}), 1)

    def test_mixed_warmup_failure_keeps_valid_student_work_and_records_failure(self):
        import school_mixed_300 as module
        from types import SimpleNamespace
        manifest = {'task_id': 1, 'course_id': 1, 'experiment_id': 2, 'question_id': 3,
                    'item_count': 20, 'usernames': ['fail', 'valid'], 'password': 'private', 'run_id': 'test'}
        args = SimpleNamespace(phase='baseline', base_url='http://localhost', timeout=35, duration=.001)
        answered = []
        def login(runner, state):
            if state.username == 'fail':
                raise RuntimeError('HTTP 503')
        with patch.object(module.MixedRunner, 'login', login), patch.object(module.MixedRunner, 'start', lambda *args: None), \
             patch.object(module.MixedRunner, 'answer', lambda runner, state: answered.append(state.username)), \
             patch.dict(sys.modules, resource=SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda _: SimpleNamespace(ru_maxrss=1))):
            result = module.phase(args, manifest)
        self.assertEqual(answered, ['valid'])
        self.assertEqual(result['student_count'], 2)
        self.assertEqual(result['completed_student_count'], 1)
        self.assertEqual(result['workflow_failures'], 1)


if __name__ == '__main__':
    unittest.main()
