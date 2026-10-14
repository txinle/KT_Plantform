#!/usr/bin/env python3
"""Independent 180-session login/start shock with the complete capture protocol."""
import argparse
from collections import Counter
import concurrent.futures
import json
import math
import random
import re
import threading
import time
from pathlib import Path

from school_short_storm import ProtocolShortRunner, StudentState, checks as short_checks, summary
from capture_protocol import BROWSER_ALIGNED_WORKLOAD, CONTRACT, evidence_files
from assessment_300 import percentile


class InternalBudgetExceeded(RuntimeError):
    pass


def admission_poll_delay(retry_after_seconds, jitter_ms=None):
    """Return the browser-equivalent poll delay without exposing credentials."""
    jitter_ms = random.randrange(500) if jitter_ms is None else jitter_ms()
    return max(1, retry_after_seconds or 3) + jitter_ms / 1000


class AttemptJournal:
    """The runner already writes every request row to this flushed private JSONL."""
    def __init__(self, output):
        self.output, self.count = output, 0

    def append(self, row):
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        if not self.output.closed:
            self.output.flush()
        with Path(self.output.name).open(encoding='utf-8') as source:
            for line in source:
                yield json.loads(line)


class ObservedResponse:
    def __init__(self, response, runner, row):
        self.response, self.runner, self.row = response, runner, row

    def __enter__(self):
        return self.response.__enter__()

    def __exit__(self, kind, value, traceback):
        try:
            return self.response.__exit__(kind, value, traceback)
        finally:
            self.runner.complete_send(self.row, kind is None)


class ObservedOpener:
    def __init__(self, opener, runner, state):
        self.opener, self.runner, self.state = opener, runner, state

    def open(self, request, timeout):
        stage = getattr(self.state, 'load_stage', '')
        path = request.full_url.removeprefix(self.runner.base_url)
        phase = None
        if stage == 'login_preparation':
            phase = 'login_page' if path == '/login/' else 'csrf'
        elif stage == 'login_post':
            phase = 'login_post'
        elif stage == 'start':
            if path == f'/api/v1/student/tasks/{self.runner.task_id}/start/' and request.get_method() == 'POST':
                operation_id = self.runner.request_context.current['operation_id']
                if not hasattr(self.state, 'admission_initial_operation_id'):
                    self.state.admission_initial_operation_id = operation_id
                if self.state.admission_initial_operation_id == operation_id:
                    phase = 'start'
            elif re.fullmatch(r'/api/v1/student/sessions/\d+/questions/\d+/(?:\?.*)?', path) and request.get_method() == 'POST':
                phase = 'first_question'
        if phase is None:
            return self.opener.open(request, timeout=timeout)
        context = self.runner.request_context.current
        planned = self.runner.phase_plans[stage]
        row = {'phase': phase, 'student_index': self.state.load_student_index,
               'operation_id': context['operation_id'], 'attempt': context['attempt'],
               'planned_at': planned['wall'], 'actual_send_at': time.time(), 'completed_at': None,
               'send_delay_seconds': time.perf_counter() - planned['perf'],
               'http_status': None, 'successful': False}
        with self.runner.lock:
            self.runner.synchronized_sends.append(row)
        try:
            response = self.opener.open(request, timeout=timeout)
            row['http_status'] = response.status
            return ObservedResponse(response, self.runner, row)
        except BaseException as error:
            row['http_status'] = getattr(error, 'code', None)
            self.runner.complete_send(row, False)
            raise


class Synchronized180Runner(ProtocolShortRunner):
    def __init__(self, *args, workload_version=CONTRACT, atomic_submission='auto', internal_budget_seconds=None, **kwargs):
        if workload_version not in (CONTRACT, BROWSER_ALIGNED_WORKLOAD) or atomic_submission not in ('auto', 'off'):
            raise ValueError('Unsupported workload or atomic submission mode')
        if internal_budget_seconds is not None and (workload_version != BROWSER_ALIGNED_WORKLOAD
                or not math.isfinite(internal_budget_seconds) or not 0 < internal_budget_seconds < 1800):
            raise ValueError('Internal budget requires browser-aligned-v2 and must be greater than zero and below 1800 seconds')
        super().__init__(*args, **kwargs)
        self.workload_version, self.atomic_submission = workload_version, atomic_submission
        self.phase_plans, self.synchronized_sends = {}, []
        self.operation_measurements = []
        self.execution_completed = False
        self.internal_budget_seconds = internal_budget_seconds
        self.budget_started = time.monotonic()
        self.budget_deadline = self.budget_started + internal_budget_seconds if internal_budget_seconds is not None else None
        self.budget_interrupted = False

    def remaining_budget(self):
        if self.budget_deadline is None:
            return None
        remaining = self.budget_deadline - time.monotonic()
        if remaining <= 0:
            self.budget_interrupted = True
            raise InternalBudgetExceeded('Internal workload budget exhausted')
        return remaining

    def enable_attempt_journal(self, output):
        if self.workload_version != BROWSER_ALIGNED_WORKLOAD or self.request_count:
            raise ValueError('Disk request journal requires a new browser-aligned runner')
        self.attempt_output = output
        self.request_attempts = AttemptJournal(output)

    def attempt_summary(self):
        if not isinstance(self.request_attempts, AttemptJournal):
            return super().attempt_summary()
        count = first = retried = recovered = 0
        latencies = []
        for row in self.request_attempts:
            latencies.append(row['elapsed_seconds'])
            if row['attempt'] == 1:
                count += 1
                first += row['successful']
            elif row['attempt'] == 2:
                retried += 1
                recovered += row['successful']
            else:
                raise RuntimeError('Unexpected retry count in the complete request journal')
        return {'operation_count': count, 'first_attempt_success_rate': first / count if count else None,
            'retry_rate': retried / count if count else None, 'recovered_operations': recovered,
            'failed_operations': count - first - recovered, 'attempt_latency': {'count': len(latencies),
                'p50_seconds': percentile(latencies, .5), 'p95_seconds': percentile(latencies, .95),
                'p99_seconds': percentile(latencies, .99), 'max_seconds': max(latencies, default=None)}}

    def complete_send(self, row, successful):
        with self.lock:
            row['completed_at'] = time.time()
            row['successful'] = successful and row['http_status'] < 400

    def phase(self, name, states, operation, workers=None, *, synchronized=True):
        if not states:
            return
        def release():
            self.released_at = time.perf_counter()
            self.phase_plans[name] = {'wall': time.time(), 'perf': self.released_at}
            if name == 'answer' and self.workload_version == BROWSER_ALIGNED_WORKLOAD:
                self.browser_answer_origin = time.monotonic()
                self.browser_answer_wall_origin = self.phase_plans[name]['wall']
        barrier = threading.Barrier(len(states), action=release)
        def invoke(state):
            state.load_stage = name
            try:
                remaining = self.remaining_budget()
                barrier.wait(timeout=min(30, remaining) if remaining is not None else 30)
                self.remaining_budget()
                operation(state)
            except Exception as error:
                if self.budget_deadline is not None and time.monotonic() >= self.budget_deadline:
                    self.budget_interrupted = True
                state.workflow_failed = True
                row = {'phase': name, 'student_index': state.load_student_index, 'error': type(error).__name__}
                match = re.fullmatch(r'HTTP (\d{3})', str(error))
                if match:
                    row['http_status'] = int(match.group(1))
                with self.lock:
                    self.errors.append(row)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(states)) as pool:
            list(pool.map(invoke, states))

    def measured_request(self, state, path, metric, *, origin=None, **kwargs):
        began = time.perf_counter() if origin is None else origin
        status, error_type, successful = None, None, False
        try:
            result = self.request(state, path, **kwargs)
            status = result[0]
            successful = status in (200, 201, 202)
            return result
        except Exception as error:
            error_type = type(error).__name__
            match = re.fullmatch(r'HTTP (\d{3})', str(error))
            if match:
                status = int(match.group(1))
            raise
        finally:
            self.record_outcome(state, metric, began, successful, status, error_type)

    def record_outcome(self, state, metric, began, successful, status, error_type):
        seconds = time.perf_counter() - began
        self.record(metric, seconds)
        with self.lock:
            self.operation_measurements.append({'metric': metric, 'student_index': state.load_student_index,
                'elapsed_seconds': seconds, 'http_status': status, 'error_type': error_type,
                'successful': successful,
                'admission_error': (({'ADMISSION_TOKEN_INVALID': 'invalid_token',
                    'ADMISSION_UNAVAILABLE': 'redis_failure', 'transport_timeout': 'timeout'}.get(
                        getattr(state, 'browser_last_error_code', None)) or
                    {409: 'invalid_token', 503: 'redis_failure'}.get(status)) if not successful
                        and metric in ('start', 'admission_poll', 'admission_start', 'admission_wait',
                                       'admitted_to_first_question_ready', 'start_to_first_question_ready') else None)})

    def _start_with_admission(self, state):
        path = f'/api/v1/student/tasks/{self.task_id}/start/'
        status, successful, error_type = None, False, None
        credentials = None
        try:
            status, payload, _ = self.measured_request(
                state, path, 'start', origin=self.released_at, method='POST', body={})
            while status == 202:
                if payload.get('status') != 'queued' or not payload.get('admission_token') or not payload.get('poll_url'):
                    raise RuntimeError('Admission response missing required fields')
                credentials = {'admission_token': payload['admission_token'], 'admission_action': 'start'}
                delay = admission_poll_delay(payload.get('retry_after_seconds'))
                remaining = self.remaining_budget()
                time.sleep(min(delay, remaining) if remaining is not None else delay)
                self.remaining_budget()
                status, payload, _ = self.measured_request(state, payload['poll_url'], 'admission_poll',
                    method='POST', body={'admission_token': credentials['admission_token'], 'action': 'start'})
                if status == 200 and payload.get('status') == 'admitted':
                    break
            if status not in (200, 201):
                raise RuntimeError('Start failed')
            state.admitted_at = time.perf_counter()
            successful = True
        except Exception as error:
            error_type = type(error).__name__
            match = re.fullmatch(r'HTTP (\d{3})', str(error))
            if match:
                status = int(match.group(1))
            raise
        finally:
            self.record_outcome(state, 'admission_wait', self.released_at, successful, status, error_type)
        if credentials is not None:
            status, payload, _ = self.measured_request(state, path, 'admission_start',
                method='POST', body=credentials)
            if status not in (200, 201):
                raise RuntimeError('Start failed')
        if payload.get('admission_token'):
            credentials = {'admission_token': payload['admission_token'],
                           'admission_action': payload.get('admission_action') or 'start'}
        state.admission_credentials = credentials
        return status, payload

    def prepare_login(self, state):
        began = time.perf_counter()
        state.load_entry_started_at = began
        try:
            context_path = ('/api/v1/auth/bootstrap/' if getattr(self, 'authentication_profile', 'legacy') == 'bootstrap-v1'
                            else '/api/v1/auth/csrf/')
            for path, metric in (('/login/', 'login_page'), (context_path, 'csrf')):
                status, _, _ = self.measured_request(state, path, metric)
                if status != 200:
                    raise RuntimeError('Login preparation failed')
            if not self._csrf(state):
                raise RuntimeError('CSRF cookie absent after preparation')
        except Exception as error:
            match = re.fullmatch(r'HTTP (\d{3})', str(error))
            self.record_outcome(state, 'login_entry_to_authentication_end', began, False,
                                int(match.group(1)) if match else None, type(error).__name__)
            raise
        finally:
            self.record('login_preparation', time.perf_counter() - began)
        state.login_prepared = True

    def authenticate(self, state):
        began = time.perf_counter()
        status, successful, error_type = None, False, None
        try:
            status, _, _ = self.measured_request(state, '/api/v1/auth/login/', 'login_post', method='POST',
                                       body={'username': state.username, 'password': state.password})
            if status != 200:
                raise RuntimeError('Authentication failed')
            state.authenticated = True
            successful = True
        except Exception as error:
            error_type = type(error).__name__
            match = re.fullmatch(r'HTTP (\d{3})', str(error))
            if match:
                status = int(match.group(1))
            raise
        finally:
            elapsed = time.perf_counter() - began
            self.record('login', elapsed)
            self.record_outcome(state, 'login_entry_to_authentication_end', state.load_entry_started_at,
                                successful, status, error_type)

    def start_and_first_question(self, state):
        status, successful, error_type = None, False, None
        try:
            with self.lock:
                self.send_delays.append(time.perf_counter() - self.released_at)
            status, payload = self._start_with_admission(state)
            state.session_id = int(payload['session_id'])
            state.assignment_ids = [int(payload['question_url'].rstrip('/').split('/')[-1])]
            path = f'/api/v1/student/sessions/{state.session_id}/questions/{state.assignment_ids[0]}/'
            state.browser_entry_material = self.entry_material(state)
            if state.admission_credentials:
                state.browser_entry_material.update(state.admission_credentials)
            status, dto, _ = self.measured_request(state, path, 'first_question', method='POST', body=state.browser_entry_material)
            process = dto.get('process', {}) if isinstance(dto, dict) else {}
            if status != 200 or process.get('capture_version') != 'capture-v2':
                raise RuntimeError('First question capture contract absent')
            state.browser_entry_material.pop('admission_token', None)
            state.browser_entry_material.pop('admission_action', None)
            state.admission_credentials = None
            if process.get('active_response_event_id') or process.get('response_version') != 0:
                raise RuntimeError('First question response is not cold')
            if self.workload_version == BROWSER_ALIGNED_WORKLOAD:
                self.browser_open_question(state, state.assignment_ids[0], dto=dto)
            state.first_question_ready = True
            successful = True
        except Exception as error:
            error_type = type(error).__name__
            match = re.fullmatch(r'HTTP (\d{3})', str(error))
            if match:
                status = int(match.group(1))
            raise
        finally:
            if getattr(state, 'admitted_at', None) is not None:
                self.record_outcome(state, 'admitted_to_first_question_ready', state.admitted_at,
                                    successful, status, error_type)
            self.record_outcome(state, 'start_to_first_question_ready', self.released_at,
                                successful, status, error_type)


def run_synchronized(runner, states):
    for index, state in enumerate(states):
        state.load_student_index = index
        state.opener = ObservedOpener(state.opener, runner, state)
    runner.phase('login_preparation', states, runner.prepare_login)
    phases = [('login_post', runner.authenticate), ('start', runner.start_and_first_question)]
    if getattr(runner, 'workload_version', CONTRACT) != BROWSER_ALIGNED_WORKLOAD:
        phases.append(('resume', runner.resume))
    phases.append(('answer', runner.answer))
    for name, operation in phases:
        valid = [state for state in states if not getattr(state, 'workflow_failed', False)]
        runner.phase(name, valid, operation)
    runner.execution_completed = not getattr(runner, 'budget_interrupted', False)


def synchronized_window(rows, phase, expected):
    first = [row for row in rows if row['phase'] == phase and row['attempt'] == 1]
    sends = [row['actual_send_at'] for row in first]
    span = max(sends) - min(sends) if sends else None
    return {'expected_student_count': expected, 'first_attempt_count': len(first),
            'actual_send_window_seconds': span,
            'concentrated': expected > 0 and len(first) == expected and span is not None and span <= 1,
            'send_delay': summary([row.get('send_delay_seconds', row['actual_send_at'] - row['planned_at']) for row in first])}


def browser_aligned_checks(result):
    n, metrics, protocol = result['student_count'], result['metrics'], result['capture_protocol']
    expected = []
    for index in range(20):
        expected.append(('answer_input', index))
        if index < 19:
            expected.extend((('navigation_next_ready', index), ('navigation_return_ready', index)))
        expected.extend((('submit_confirmation_ready', index), ('submit_next_ready', index)))
    actions = protocol.get('logical_actions', [])
    identities = {row['student_index'] for row in actions}
    matched = len(identities) == n and all(
        [(row['action'], row['item_index']) for row in actions if row['student_index'] == identity] == expected
        for identity in identities)
    def bound(metric, percentile, limit):
        value = metrics.get(metric, {}).get(percentile)
        return value is not None and value <= limit
    checks = {'complete_workflow': matched and protocol.get('completed_sessions') == n,
        'capture_protocol_complete': (protocol.get('workload_version') == BROWSER_ALIGNED_WORKLOAD
            and protocol.get('all_events_acknowledged') is True and protocol.get('all_checkpoints_acknowledged') is True),
        'frozen_logical_probe_actions': matched, 'zero_workflow_errors': not result['errors'],
        'zero_http_errors': result['http_error_count'] == 0,
        'all_unique_sessions': result['session_count'] == n and result['unique_session_count'] == n}
    for metric, p95, p99 in (('start_to_first_question_ready', 5, 10), ('save_draft', 1, 2), ('event_batch', 1, 2),
                            ('navigation_next_ready', 2, 5), ('navigation_return_ready', 2, 5),
                            ('submit_next_ready', 3, 5), ('finish_ready', 3, 5)):
        checks[f'{metric}_p95'] = bound(metric, 'p95_seconds', p95)
        checks[f'{metric}_p99'] = bound(metric, 'p99_seconds', p99)
    return checks


def full_operation_accounting(rows, students):
    counts = {'login_entry_to_authentication_end': 1, 'start_to_first_question_ready': 1,
        'admission_wait': 1, 'admitted_to_first_question_ready': 1,
        'answer_input': 20, 'navigation_next_ready': 19, 'navigation_return_ready': 19,
        'submit_confirmation_ready': 20, 'submit_next_ready': 20, 'finish_ready': 1}
    result = {}
    for metric, per_student in counts.items():
        observed = [row for row in rows if row['metric'] == metric]
        successful = sum(row['successful'] for row in observed)
        result[metric] = {'planned': students * per_student, 'attempted': len(observed),
            'successful': successful, 'failed': len(observed) - successful,
            'not_reached': max(0, students * per_student - len(observed)),
            'excess_attempts': max(0, len(observed) - students * per_student),
            'all_attempted_latency': summary([row['elapsed_seconds'] for row in observed]),
            'successful_only_latency': summary([row['elapsed_seconds'] for row in observed if row['successful']])}
    return result


def build_result(runner, states, started, ended, *, expected_students=180):
    n = expected_students
    failure_types, failure_statuses = Counter(), Counter()
    timed_out_operations = set()
    for attempt in runner.request_attempts:
        if not attempt['successful']:
            failure_types[attempt.get('error_type') or 'unknown'] += 1
            if attempt.get('http_status') is not None:
                failure_statuses[str(attempt['http_status'])] += 1
            if attempt.get('error_type') in ('TimeoutError', 'timeout'):
                timed_out_operations.add(attempt['operation_id'])
    authenticated = sum(getattr(state, 'authenticated', False) for state in states)
    windows = {phase: synchronized_window(runner.synchronized_sends, phase, authenticated if phase == 'start' else n)
               for phase in ('login_page', 'csrf', 'login_post', 'start', 'first_question')}
    operation_latency, attempt_latency = {}, {}
    for metric in ('login_page', 'csrf', 'login_post', 'start', 'first_question'):
        operations = [row for row in runner.operation_measurements if row['metric'] == metric]
        operation_latency[metric] = {
            'all_operations': summary([row['elapsed_seconds'] for row in operations]),
            'successful_only': summary([row['elapsed_seconds'] for row in operations if row['successful']])}
        identities = {row['operation_id'] for row in runner.synchronized_sends if row['phase'] == metric}
        attempts = [row for row in runner.request_attempts if row['operation_id'] in identities]
        final_attempts = {}
        for row in attempts:
            previous = final_attempts.get(row['operation_id'])
            if previous is None or row['attempt'] > previous['attempt']:
                final_attempts[row['operation_id']] = row
        attempt_latency[metric] = {
            'all_attempts': summary([row['elapsed_seconds'] for row in attempts]),
            'first_attempts': summary([row['elapsed_seconds'] for row in attempts if row['attempt'] == 1]),
            'retry_attempts': summary([row['elapsed_seconds'] for row in attempts if row['attempt'] > 1]),
            'final_attempts': summary([row['elapsed_seconds'] for row in final_attempts.values()]),
            'successful_only': summary([row['elapsed_seconds'] for row in attempts if row['successful']])}
    for metric in ('login_entry_to_authentication_end', 'start_to_first_question_ready',
                   'admission_wait', 'admitted_to_first_question_ready'):
        operations = [row for row in runner.operation_measurements if row['metric'] == metric]
        operation_latency[metric] = {
            'all_operations': summary([row['elapsed_seconds'] for row in operations]),
            'successful_only': summary([row['elapsed_seconds'] for row in operations if row['successful']])}
    result = {'mode': 'isolated_synchronized_180', 'formal_capacity_accepted': False,
        'workload_version': getattr(runner, 'workload_version', CONTRACT),
        'student_count': n, 'started': started, 'ended': ended, 'item_interval_seconds': 18,
        'login_preparation_success_count': sum(getattr(state, 'login_prepared', False) for state in states),
        'authentication_success_count': authenticated,
        'started_student_count': sum(state.session_id is not None for state in states),
        'first_question_success_count': sum(getattr(state, 'first_question_ready', False) for state in states),
        'metrics': {key: summary(values) for key, values in runner.metrics.items()},
        'errors': runner.errors, 'http_request_count': runner.request_count, 'http_error_count': runner.request_errors,
        'request_failures': {'error_types': dict(failure_types), 'http_statuses': dict(failure_statuses),
            'timeout_attempts': failure_types['TimeoutError'] + failure_types['timeout'],
            'timeout_operations': len(timed_out_operations),
            'incomplete_student_count': n - runner.protocol_summary()['completed_sessions']},
        'http_requests_per_second': runner.request_count / (ended - started) if ended > started else None,
        'attempt_summary': runner.attempt_summary(), 'synchronized_windows': windows,
        'operation_latency': operation_latency, 'operation_measurements': runner.operation_measurements,
        'request_attempt_latency': attempt_latency,
        'send_observation_boundary': 'Timestamp immediately before urllib opener.open; socket/TLS transmission is not directly observed.',
        'end_to_end_boundaries': {
            'login_entry_to_authentication_end': 'Per identity login-page preparation invocation through final authentication outcome, including waiting for other preparation and the POST barrier.',
            'start_to_first_question_ready': 'Shared start barrier release through formal question-entry POST and cold-state validation, including admission polling, retries and preceding sender delay.'},
        'synchronized_sends': runner.synchronized_sends,
        'item_arrivals': runner.item_arrivals,
        'session_count': sum(state.session_id is not None for state in states),
        'unique_session_count': len({state.session_id for state in states if state.session_id is not None}),
        'generator': {'start_send_delay': summary(runner.send_delays),
            'item_arrival_delay_interpretation': 'Cumulative schedule lag includes previous service waits; it is not pure client queueing.'},
        'capture_protocol': runner.protocol_summary(), 'execution_completed': runner.execution_completed,
        'workflow_completed': not runner.errors and runner.protocol_summary()['completed_sessions'] == n,
        'data_integrity_passed': None, 'data_integrity_state': 'pending_external_verification', 'safety_interrupted': False}
    if result['workload_version'] == BROWSER_ALIGNED_WORKLOAD:
        result['full_operation_accounting'] = full_operation_accounting(
            runner.operation_measurements + result['capture_protocol']['operation_measurements'], n)
        result['internal_budget_seconds'] = runner.internal_budget_seconds
        result['budget_interrupted'] = runner.budget_interrupted
        result['safety_interrupted'] = runner.budget_interrupted
        result['incomplete'] = runner.budget_interrupted or not result['workflow_completed']
        result['atomic_submission_mode'] = runner.atomic_submission
        result['generator']['item_arrival_delay_interpretation'] = (
            'Fixed 18-second item action arrivals share the answer-phase start origin. Generator lateness is observed invocation minus planned arrival; service waits can make later arrivals late.')
        result['generator']['item_arrival_lateness'] = summary([row['generator_lateness_seconds'] for row in runner.item_arrivals])
        result['end_to_end_boundaries']['start_to_first_question_ready'] = (
            'Shared start barrier through admission polling, authorized entry DTO, resume checkpoint, time synchronization, live offline authorization, versioned generic shell/assets and research tool setup. Independent initial RPCs are serialized conservatively; rendering needs a separate browser witness.')
        checks = browser_aligned_checks(result)
    else:
        checks = short_checks(result)
    def bound(metric, key, limit):
        value = result['metrics'].get(metric, {}).get(key)
        return value is not None and value <= limit
    checks.update({'all_180_prepared': result['login_preparation_success_count'] == n,
        'all_180_authenticated': authenticated == n, 'all_180_first_questions': result['first_question_success_count'] == n,
        'login_first_attempt_window_at_most_one_second': windows['login_post']['concentrated'],
        'start_first_attempt_window_at_most_one_second': windows['start']['concentrated'],
        'login_p95': bound('login_entry_to_authentication_end', 'p95_seconds', 5),
        'login_p99': bound('login_entry_to_authentication_end', 'p99_seconds', 10)})
    result['checks'] = checks
    result['login_load_achieved_180'] = windows['login_post']['concentrated']
    result['start_load_achieved_180'] = windows['start']['first_attempt_count'] == n and windows['start']['concentrated']
    result['valid_synchronized_login'] = result['login_load_achieved_180']
    result['valid_synchronized_start'] = result['start_load_achieved_180']
    result['capacity_start_ready_180'] = (authenticated == n and result['started_student_count'] == n
        and result['first_question_success_count'] == n)
    result['performance_passed'] = all(checks.values())
    result['passed'] = result['performance_passed']
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--timeout', type=float, default=35)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workload-version', choices=(CONTRACT, BROWSER_ALIGNED_WORKLOAD), default=CONTRACT)
    parser.add_argument('--atomic-submission', choices=('auto', 'off'), default='auto',
                        help='auto uses only the advertised version-1 capability; off retains standalone uploads')
    parser.add_argument('--internal-budget-seconds', type=float, default=None,
                        help='browser-aligned-v2 only: stop new RPCs before this monotonic budget below 1800 seconds')
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    import resource
    if not 0 < args.timeout <= 35:
        parser.error('timeout must be greater than zero and at most 35 seconds')
    if args.internal_budget_seconds is not None and (args.workload_version != BROWSER_ALIGNED_WORKLOAD
            or not math.isfinite(args.internal_budget_seconds) or not 0 < args.internal_budget_seconds < 1800):
        parser.error('internal budget requires browser-aligned-v2 and must be greater than zero and below 1800 seconds')
    path = Path(args.manifest)
    fixture = json.loads(path.read_text(encoding='utf-8-sig'))
    if (fixture.get('database') != 'kt_e2e' or fixture.get('capture_protocol') != CONTRACT
            or fixture.get('student_count') != 180 or fixture.get('item_count') != 20
            or len(fixture.get('usernames', [])) != 180
            or len(set(fixture.get('usernames', []))) != 180):
        parser.error('a fresh isolated 180-student / 20-item capture fixture is required')
    runner = Synchronized180Runner(args.base_url, fixture['task_id'], args.timeout, item_interval=18,
                                  workload_version=args.workload_version, atomic_submission=args.atomic_submission,
                                  internal_budget_seconds=args.internal_budget_seconds)
    runner.protocol_course_id, runner.protocol_experiment_id = fixture['course_id'], fixture['experiment_id']
    if args.workload_version == BROWSER_ALIGNED_WORKLOAD:
        runner.browser_shell_url = fixture['source_config']['shell_url']
    states = [StudentState(name, fixture['password']) for name in fixture['usernames']]
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.time()
    with evidence_files(path, args.output) as files:
        files['runner'] = runner
        if args.workload_version == BROWSER_ALIGNED_WORKLOAD:
            runner.enable_attempt_journal(files['attempts'])
        else:
            runner.attempt_output = files['attempts']
        run_synchronized(runner, states)
        result = build_result(runner, states, started, time.time())
        after = resource.getrusage(resource.RUSAGE_SELF)
        result['source_config'] = fixture['source_config']
        result['generator'].update(cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                                   max_rss_bytes=after.ru_maxrss * 1024)
        result['request_attempts_file'] = Path(args.output).with_suffix('.attempts.jsonl').name
        result['operation_measurements_file'] = Path(args.output).with_suffix('.operations.jsonl').name
        result['progress_file'] = Path(args.output).with_suffix('.progress.json').name
        files['complete'] = result['workflow_completed']
        json.dump(result, files['result'], indent=2)
        files['result_ready'] = True
    print(json.dumps({key: result[key] for key in ('student_count', 'authentication_success_count',
        'execution_completed', 'workflow_completed', 'performance_passed', 'data_integrity_passed', 'checks')}))
    raise SystemExit(0 if result['performance_passed'] else 1)


if __name__ == '__main__':
    main()
