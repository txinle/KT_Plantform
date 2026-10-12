import importlib.util
import io
import json
import sys
import threading
import time
import unittest
import urllib.error
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status, self.payload = status, {'data': payload, 'meta': {'request_id': 'fixture'}}
        self.headers = {'Content-Type': 'application/json', 'X-Request-ID': 'fixture'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.payload).encode()


class SchoolSynchronized180Tests(unittest.TestCase):
    def test_timeout_attempts_are_counted_even_when_no_operation_completes(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
        state = module.StudentState('private', 'private')
        state.load_student_index = 0
        def timeout(request, timeout):
            raise TimeoutError('bounded request')
        state.opener = SimpleNamespace(open=timeout)
        with self.assertRaises(RuntimeError):
            runner._browser_request(state, '/test/', 'test')
        result = module.build_result(runner, [state], 1, 2)
        self.assertEqual(result['request_failures']['timeout_attempts'], 2)
        self.assertEqual(result['request_failures']['timeout_operations'], 1)
        self.assertEqual(result['request_failures']['error_types'], {'TimeoutError': 2})
        self.assertEqual(result['student_count'], 180)

    def test_failed_login_preparation_is_a_failed_full_login_operation(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
        state = module.StudentState('private', 'private')
        state.load_student_index = 0
        with patch.object(runner, 'measured_request', side_effect=TimeoutError('request timed out')):
            with self.assertRaises(TimeoutError):
                runner.prepare_login(state)
        rows = [row for row in runner.operation_measurements if row['metric'] == 'login_entry_to_authentication_end']
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['successful'])
        self.assertEqual(rows[0]['error_type'], 'TimeoutError')

    def test_full_operation_accounting_keeps_failed_and_unreached_students(self):
        module = self.module()
        rows = [
            {'metric': 'navigation_next_ready', 'successful': True, 'elapsed_seconds': .2},
            {'metric': 'navigation_next_ready', 'successful': False, 'elapsed_seconds': 35},
        ]
        result = module.full_operation_accounting(rows, 180)
        self.assertEqual(result['navigation_next_ready']['planned'], 3420)
        self.assertEqual(result['navigation_next_ready']['attempted'], 2)
        self.assertEqual(result['navigation_next_ready']['successful'], 1)
        self.assertEqual(result['navigation_next_ready']['failed'], 1)
        self.assertEqual(result['navigation_next_ready']['not_reached'], 3418)
        self.assertEqual(result['finish_ready']['not_reached'], 180)
        self.assertEqual(result['submit_next_ready']['planned'], 3600)

    def test_internal_budget_is_explicit_browser_only_and_caps_final_rpc_timeout(self):
        module = self.module()
        self.assertIsNone(module.build_parser().parse_args(['--manifest', 'm', '--base-url', 'http://local', '--output', 'o']).internal_budget_seconds)
        with self.assertRaises(ValueError):
            module.Synchronized180Runner('http://local', 1, 35, internal_budget_seconds=1750)
        for limit in (0, 1800, float('nan')):
            with self.assertRaises(ValueError):
                module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2', internal_budget_seconds=limit)
        with patch.object(module.time, 'monotonic', return_value=100):
            runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2', internal_budget_seconds=2)
        state = module.StudentState('private', 'private')
        sent = []
        state.opener = SimpleNamespace(open=lambda request, timeout: sent.append(timeout) or FakeResponse({}))
        with patch.object(module.time, 'monotonic', return_value=101.25):
            runner._browser_request(state, '/test/', 'test')
        self.assertEqual(sent, [.75])
        with patch.object(module.time, 'monotonic', return_value=102):
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                runner._browser_request(state, '/not-sent/', 'test')
        self.assertEqual(sent, [.75])
        self.assertTrue(runner.budget_interrupted)
        self.assertEqual(len(runner.protocol_summary()['operation_measurements']), 2)

    def test_disk_request_journal_preserves_all_rows_and_exact_retry_summary_without_ram_copy(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
        state = module.StudentState('private', 'private')
        count = 0
        def open_request(request, timeout):
            nonlocal count
            count += 1
            if count == 1:
                raise urllib.error.HTTPError(request.full_url, 500, 'failure', {}, None)
            return FakeResponse({})
        state.opener = SimpleNamespace(open=open_request)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.jsonl'
            with path.open('x', encoding='utf-8') as output:
                runner.enable_attempt_journal(output)
                runner._browser_request(state, '/test/', 'test')
                self.assertEqual(len(runner.request_attempts), 2)
                self.assertNotIsInstance(runner.request_attempts, list)
                rows = list(runner.request_attempts)
                self.assertEqual(len(rows), 2)
                self.assertFalse(rows[0]['successful'])
                self.assertTrue(rows[1]['successful'])
                result = runner.attempt_summary()
                self.assertEqual((result['operation_count'], result['retry_rate'], result['recovered_operations'], result['failed_operations']), (1, 1, 1, 0))
            self.assertEqual(len(path.read_text().splitlines()), 2)

    def test_budget_stops_all_threads_and_writes_partial_acked_ledger_and_failure_timings(self):
        module = self.module()
        from capture_protocol import BrowserAlignedStream, evidence_files
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2', internal_budget_seconds=.15)
        states = [module.StudentState(f'private{index}', 'private') for index in range(4)]
        actual_sends = []
        for index, state in enumerate(states):
            state.session_id, state.load_student_index = index + 1, index
            def open_request(request, timeout):
                actual_sends.append(timeout)
                if request.full_url.endswith('/wait/'):
                    time.sleep(timeout)
                    raise TimeoutError('simulated socket timeout')
                body = json.loads(request.data)
                event = body['events'][0]
                return FakeResponse({'success': True, 'acknowledgements': [{'event_id': event['event_id'],
                    'client_sequence_no': event['client_sequence_no'], 'server_ingest_sequence_no': 1}]})
            state.opener = SimpleNamespace(open=open_request)
        def operation(state):
            stream = BrowserAlignedStream(state.session_id)
            event = stream.event(21, 'document_visible', {})
            runner._browser_request(state, '/api/v1/assessment/process/capture-v2/batch/', 'event_batch', method='POST',
                body={'client_instance_id': stream.client_instance_id, 'events': [event]})
            runner._browser_request(state, '/wait/', 'timed_out_rpc')
        with tempfile.TemporaryDirectory() as directory:
            manifest, output = Path(directory) / 'fixture.json', Path(directory) / 'result.json'
            manifest.write_text(json.dumps({'task_id': 1, 'student_count': 4, 'capture_protocol': module.CONTRACT}))
            began = time.monotonic()
            with evidence_files(manifest, output) as files:
                files['runner'] = runner
                runner.enable_attempt_journal(files['attempts'])
                runner.budget_deadline = time.monotonic() + .15
                runner.phase('answer', states, operation)
                runner.execution_completed = not runner.budget_interrupted
                result = module.build_result(runner, states, 1, 2)
                files['complete'] = result['workflow_completed']
                json.dump(result, files['result'])
            self.assertLess(time.monotonic() - began, 2)
            self.assertTrue(result['budget_interrupted'])
            self.assertFalse(result['workflow_completed'])
            self.assertFalse(result['execution_completed'])
            self.assertFalse(result['passed'])
            ledger = json.loads(manifest.with_suffix('.ledger.json').read_text())
            self.assertTrue(ledger['incomplete'])
            self.assertEqual(len(ledger['sessions']), 4)
            self.assertTrue(all(row['events'] and all(event['acknowledged'] for event in row['events'].values()) for row in ledger['sessions'].values()))
            self.assertTrue(all(timeout <= .15 for timeout in actual_sends))
            self.assertEqual(len(actual_sends), 8)
            self.assertEqual(sum(not row['successful'] for row in result['capture_protocol']['operation_measurements']), 4)
            attempts = output.with_suffix('.attempts.jsonl').read_text().splitlines()
            self.assertEqual(len(attempts), 12)


    def module(self):
        path = ROOT / 'scripts/load/school_synchronized_180.py'
        self.assertTrue(path.exists(), 'synchronized 180 entry missing')
        spec = importlib.util.spec_from_file_location('school_synchronized_180', path)
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(path.parent), *sys.path]):
            spec.loader.exec_module(module)
        return module

    def workflow(self, failed_username=None, failed_phase='login_post'):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 123, 35, item_interval=18)
        runner.protocol_course_id, runner.protocol_experiment_id = 5, 6
        with patch('urllib.request.build_opener', side_effect=lambda *args: SimpleNamespace()):
            states = [module.StudentState(str(index), 'private') for index in range(180)]
        completed_csrf, authenticated, answered, calls = set(), set(), [], []
        lock = threading.Lock()
        def opener(state):
            def open_request(request, timeout):
                path = request.full_url.removeprefix('http://local')
                method = request.get_method()
                with lock:
                    calls.append((state.username, method, path))
                    if path == '/login/':
                        self.assertNotIn(state.username, authenticated)
                        return FakeResponse({})
                    if path == '/api/v1/auth/csrf/':
                        completed_csrf.add(state.username)
                        state.cookies = [SimpleNamespace(name='csrftoken', value='token')]
                        return FakeResponse({})
                    if path == '/api/v1/auth/login/':
                        self.assertEqual(len(completed_csrf), 180)
                        self.assertEqual(method, 'POST')
                        if state.username == failed_username and failed_phase == 'login_post':
                            raise urllib.error.HTTPError(request.full_url, 500, 'failed', {}, None)
                        authenticated.add(state.username)
                        return FakeResponse({})
                    self.assertIn(state.username, authenticated)
                    identity = int(state.username) + 1
                    if path.endswith('/start/'):
                        if state.username == failed_username and failed_phase == 'start':
                            raise urllib.error.HTTPError(request.full_url, 500, 'failed', {}, None)
                        time.sleep(.001)
                        return FakeResponse({'session_id': identity, 'question_url': f'/question/{identity}/'}, 201)
                    if path.endswith('/resume/'):
                        return FakeResponse({'session_id': identity})
                    if state.username == failed_username and failed_phase == 'first_question':
                        raise urllib.error.HTTPError(request.full_url, 500, 'failed', {}, None)
                    time.sleep(.001)
                    return FakeResponse({'process': {'capture_version': 'capture-v2', 'response_version': 0,
                        'active_response_event_id': None}})
            return SimpleNamespace(open=open_request)
        for state in states:
            state.opener = opener(state)
        def answer(state):
            # Real capture answer remains inherited; this isolates orchestration.
            with lock:
                answered.append(state.username)
        with patch.object(runner, 'answer', side_effect=answer):
            module.run_synchronized(runner, states)
        return module, runner, states, calls, answered

    def test_180_sessions_prepare_before_true_login_posts_and_start_first_question(self):
        module, runner, states, calls, answered = self.workflow()
        self.assertEqual(len({id(state.cookies) for state in states}), 180)
        login = [row for row in runner.synchronized_sends if row['phase'] == 'login_post' and row['attempt'] == 1]
        self.assertEqual(len(login), 180)
        self.assertEqual(len({row['planned_at'] for row in login}), 1)
        self.assertTrue(all(row['completed_at'] >= row['actual_send_at'] >= row['planned_at'] for row in login))
        self.assertEqual(len(answered), 180)
        self.assertEqual(runner.metrics['first_question'].__len__(), 180)
        self.assertEqual(runner.metrics['login'].__len__(), 180)
        for state in states:
            own = [row[2] for row in calls if row[0] == state.username]
            start = own.index('/api/v1/student/tasks/123/start/')
            self.assertIn('/questions/', own[start + 1])
        result = module.build_result(runner, states, 1, 2)
        self.assertEqual(result['student_count'], 180)
        self.assertEqual(result['authentication_success_count'], 180)
        self.assertEqual(result['synchronized_windows']['login_post']['first_attempt_count'], 180)

    def test_failed_authentication_retries_twice_and_does_not_cancel_other_students(self):
        module, runner, states, calls, answered = self.workflow('0')
        failed_posts = [row for row in calls if row == ('0', 'POST', '/api/v1/auth/login/')]
        self.assertEqual(len(failed_posts), 2)
        self.assertEqual(len(answered), 179)
        self.assertNotIn('0', answered)
        result = module.build_result(runner, states, 1, 2)
        self.assertTrue(result['execution_completed'])
        self.assertFalse(result['workflow_completed'])
        self.assertFalse(result['performance_passed'])
        self.assertIsNone(result['data_integrity_passed'])
        self.assertEqual(result['authentication_success_count'], 179)
        self.assertEqual(result['student_count'], 180)
        self.assertFalse(result['checks']['all_180_authenticated'])
        self.assertFalse(result['valid_synchronized_start'])
        self.assertTrue(result['login_load_achieved_180'])
        self.assertTrue(result['valid_synchronized_login'])
        self.assertFalse(result['start_load_achieved_180'])
        self.assertEqual(result['attempt_summary']['failed_operations'], 1)
        self.assertEqual(result['operation_latency']['login_post']['all_operations']['count'], 180)
        self.assertEqual(result['operation_latency']['login_post']['successful_only']['count'], 179)
        self.assertEqual(result['request_attempt_latency']['login_post']['all_attempts']['count'], 181)
        self.assertEqual(result['request_attempt_latency']['login_post']['successful_only']['count'], 179)

    def test_failed_start_or_first_question_is_timed_and_other_students_continue(self):
        for metric in ('start', 'first_question'):
            with self.subTest(metric=metric):
                module, runner, states, calls, answered = self.workflow('0', metric)
                self.assertEqual(len(answered), 179)
                result = module.build_result(runner, states, 1, 2)
                self.assertEqual(result['operation_latency'][metric]['all_operations']['count'], 180)
                self.assertEqual(result['operation_latency'][metric]['successful_only']['count'], 179)
                self.assertEqual(result['request_attempt_latency'][metric]['all_attempts']['count'], 181)
                self.assertEqual(result['request_attempt_latency'][metric]['final_attempts']['count'], 180)
                self.assertTrue(result['valid_synchronized_start'])
                self.assertFalse(result['capacity_start_ready_180'])
                self.assertEqual(result['operation_latency']['start_to_first_question_ready']['all_operations']['count'], 180)
                self.assertEqual(result['operation_latency']['start_to_first_question_ready']['successful_only']['count'], 179)
                failed = [row for row in runner.operation_measurements if row['metric'] == metric and not row['successful']]
                self.assertEqual(failed[0]['http_status'], 500)
                self.assertEqual(failed[0]['error_type'], 'RuntimeError')

    def test_start_to_ready_and_entry_to_login_include_all_prior_steps(self):
        module, runner, states, calls, answered = self.workflow()
        by_student = {}
        for row in runner.operation_measurements:
            by_student.setdefault(row['student_index'], {})[row['metric']] = row
        for row in by_student.values():
            self.assertGreaterEqual(row['start_to_first_question_ready']['elapsed_seconds'],
                                    row['start']['elapsed_seconds'] + row['first_question']['elapsed_seconds'])
            self.assertGreaterEqual(row['login_entry_to_authentication_end']['elapsed_seconds'],
                                    row['login_page']['elapsed_seconds'] + row['csrf']['elapsed_seconds'] + row['login_post']['elapsed_seconds'])

    def test_window_failure_is_separate_from_successful_authentication(self):
        module = self.module()
        rows = [{'phase': 'login_post', 'attempt': 1, 'actual_send_at': 10 + index / 100,
                 'planned_at': 10, 'completed_at': 13} for index in range(180)]
        window = module.synchronized_window(rows, 'login_post', 180)
        self.assertEqual(window['first_attempt_count'], 180)
        self.assertGreater(window['actual_send_window_seconds'], 1)
        self.assertFalse(window['concentrated'])

    def test_admission_queued_poll_jitter_then_repeats_start_and_consumes_token_on_first_entry(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
        state = module.StudentState('private', 'private')
        state.load_student_index = 0
        runner.released_at = time.perf_counter()
        calls = []
        responses = iter([
            FakeResponse({'status': 'queued', 'admission_token': 'secret', 'poll_url': '/api/v1/student/tasks/1/admission/status/', 'retry_after_seconds': 3}, 202),
            FakeResponse({'status': 'admitted', 'admission_token': 'secret', 'poll_url': '/api/v1/student/tasks/1/admission/status/', 'retry_after_seconds': 3}, 200),
            FakeResponse({'session_id': 7, 'question_url': '/question/11/'}, 201),
            FakeResponse({'process': {'capture_version': 'capture-v2', 'response_version': 0, 'active_response_event_id': None}}, 200),
        ])
        def open_request(request, timeout):
            calls.append((request.full_url, json.loads(request.data) if request.data else None))
            return next(responses)
        state.opener = SimpleNamespace(open=open_request)
        runner.entry_material = lambda state: {'reason': 'direct_open'}
        with patch.object(module.random, 'randrange', return_value=321) as jitter, patch.object(module.time, 'sleep') as sleep, patch.object(runner, 'browser_open_question') as opened:
            runner.start_and_first_question(state)
        sleep.assert_called_once_with(3.321)
        opened.assert_called_once()
        self.assertEqual(calls[3][1]['admission_token'], 'secret')
        self.assertEqual(calls[3][1]['admission_action'], 'start')
        self.assertEqual(calls[0][1], {})
        self.assertEqual(calls[1][1], {'admission_token': 'secret', 'action': 'start'})
        self.assertEqual(calls[2][1], {'admission_token': 'secret', 'admission_action': 'start'})
        self.assertEqual(jitter.call_count, 1)
        self.assertEqual(runner.metrics['admission_wait'].__len__(), 1)
        self.assertEqual(runner.metrics['admitted_to_first_question_ready'].__len__(), 1)
        self.assertNotIn('admission_token', state.browser_entry_material)
        self.assertNotIn('admission_action', state.browser_entry_material)
        self.assertIsNone(state.admission_credentials)
        self.assertEqual(runner.request_errors, 0)
        self.assertTrue(all(row['successful'] for row in runner.operation_measurements))
        self.assertNotIn('secret', json.dumps(module.build_result(runner, [state], 1, 2)))

    def test_admission_jitter_is_bounded(self):
        module = self.module()
        self.assertEqual(module.admission_poll_delay(3, lambda: 0), 3)
        self.assertGreaterEqual(module.admission_poll_delay(3, lambda: 499), 3)
        self.assertLess(module.admission_poll_delay(3, lambda: 499), 3.5)

    def test_admission_failure_is_classified_without_token(self):
        for status, code, expected in ((409, 'ADMISSION_TOKEN_INVALID', 'invalid_token'),
                                       (503, 'ADMISSION_UNAVAILABLE', 'redis_failure'),
                                       (None, None, 'timeout')):
            with self.subTest(expected=expected):
                self.admission_failure(status, code, expected)

    def admission_failure(self, status, code, expected):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
        state = module.StudentState('private', 'private')
        state.load_student_index = 0
        runner.released_at = time.perf_counter()
        def fail(request, timeout):
            if status is None:
                raise TimeoutError('secret')
            raise urllib.error.HTTPError(request.full_url, status, 'failure', {}, io.BytesIO(json.dumps({'error': {'code': code, 'admission_token': 'secret'}}).encode()))
        state.opener = SimpleNamespace(open=fail)
        with self.assertRaises(RuntimeError):
            runner.start_and_first_question(state)
        row = [r for r in runner.operation_measurements if r['metric'] == 'start_to_first_question_ready'][0]
        self.assertEqual(row['admission_error'], expected)
        self.assertNotIn('secret', json.dumps(row))
        result = module.full_operation_accounting(runner.operation_measurements, 1)
        self.assertEqual(result['admission_wait']['attempted'], 1)

    def test_immediate_admission_token_and_cold_first_entry_contract(self):
        module = self.module()
        for version in (0, 1):
            with self.subTest(version=version):
                runner = module.Synchronized180Runner('http://local', 1, 35, workload_version='browser-aligned-v2')
                state = module.StudentState('private', 'private')
                state.load_student_index = 0
                runner.released_at = time.perf_counter()
                calls = []
                def open_request(request, timeout):
                    calls.append(json.loads(request.data))
                    if request.full_url.endswith('/start/'):
                        return FakeResponse({'session_id': 7, 'question_url': '/question/11/',
                            'admission_token': 'secret', 'admission_action': 'start'}, 201)
                    return FakeResponse({'process': {'capture_version': 'capture-v2', 'response_version': version,
                        'active_response_event_id': None}})
                state.opener = SimpleNamespace(open=open_request)
                with patch.object(runner, 'browser_open_question') as opened, patch.object(module.time, 'sleep') as sleep:
                    if version:
                        with self.assertRaisesRegex(RuntimeError, 'not cold'):
                            runner.start_and_first_question(state)
                        opened.assert_not_called()
                    else:
                        runner.start_and_first_question(state)
                        opened.assert_called_once()
                    sleep.assert_not_called()
                self.assertEqual(calls[1]['admission_token'], 'secret')
                self.assertNotIn('admission_token', state.browser_entry_material)
                self.assertNotIn('admission_token', runner.entry_material(state))
                self.assertEqual(len(runner.metrics['admission_wait']), 1)
                self.assertEqual(len(runner.metrics['admitted_to_first_question_ready']), 1)

    def test_observed_start_keeps_initial_retries_but_excludes_admitted_repeat(self):
        module = self.module()
        runner = module.Synchronized180Runner('http://local', 1, 35)
        runner.phase_plans['start'] = {'wall': time.time(), 'perf': time.perf_counter()}
        state = module.StudentState('private', 'private')
        state.load_student_index, state.load_stage = 0, 'start'
        import urllib.request
        request = urllib.request.Request('http://local/api/v1/student/tasks/1/start/', data=b'{}', method='POST')
        opener = module.ObservedOpener(SimpleNamespace(open=lambda request, timeout: FakeResponse({})), runner, state)
        for operation, attempt in (('initial', 1), ('initial', 2), ('admitted', 1)):
            runner.request_context.current = {'operation_id': operation, 'attempt': attempt}
            with opener.open(request, 35):
                pass
        self.assertEqual(len(runner.synchronized_sends), 2)
        self.assertEqual({row['operation_id'] for row in runner.synchronized_sends}, {'initial'})
        self.assertEqual(module.synchronized_window(runner.synchronized_sends, 'start', 1)['first_attempt_count'], 1)

if __name__ == '__main__':
    unittest.main()
