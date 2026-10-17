"""Frozen full-paper HTTP workload with independent capture and pointer evidence.

The v2 driver is retained unchanged as a historical control. Protocol telemetry
is synthetic; browser rendering and usability require separate browser records.
"""
import argparse
import copy
import hashlib
import json
import random
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import student_capacity_protocol as v2
import student_concurrency_protocol as baseline
from student_concurrency_protocol import ProtocolFailure, checked, digest, empty_value, origin, private_output, values
from student_concurrency import _stats
from capacity_retest import (MEASUREMENT_VERSION, METRIC_SCHEMA_VERSION, WORKLOAD_PROFILE,
                             HISTORICAL_PROFILE, metric_statistics)

VERSION = 'student-capacity-protocol-v3'
MODES = (*v2.MODES, 'entry-mixed')
WORKLOADS = ('comparison-2q', 'paper-20q')


def build_plan(fixture, *, mode, count, run_id, fixture_sha256, base_url,
               workload='paper-20q', actor_ids=None, browser_account_ids=(), workload_profile=WORKLOAD_PROFILE):
    if (workload_profile not in (WORKLOAD_PROFILE, HISTORICAL_PROFILE)
            or workload_profile == WORKLOAD_PROFILE and (mode != 'full-flow' or count not in (50, 180) or workload != 'paper-20q')):
        raise ValueError('Current pressure requires full-flow, twenty questions and 50/180 actors')
    target = origin(base_url)
    accounts = fixture['accounts']
    if (mode not in MODES or workload not in WORKLOADS or count not in (3, 20, 50, 180, 240, 360)
            or count > len(accounts)):
        raise ValueError('Require a bounded capacity workload and denominator')
    if (fixture.get('isolated_demo') is not True or fixture.get('database') != 'kt_e2e'
            or fixture.get('source_ownership', {}).get('command') != 'prepare_student_capacity_baseline'
            or digest(fixture['fixture_spec']) != fixture['plan_sha']
            or not fixture.get('baseline_snapshot_id') or not run_id):
        raise ValueError('Require unchanged isolated baseline fixture and snapshot identity')
    if (fixture.get('fixture_id') != fixture['plan_sha']
            or fixture['fixture_spec'].get('content_binding_sha256') != digest(accounts)):
        raise ValueError('Full baseline account material binding differs')
    if any(len({a[k] for a in accounts}) != len(accounts) for k in ('account_id', 'username', 'user_id', 'session_id')):
        raise ValueError('Duplicate baseline identities')
    by_id = {a['account_id']: a for a in accounts}
    selected = list(actor_ids) if actor_ids is not None else [a['account_id'] for a in accounts[:count]]
    browser_ids = list(browser_account_ids)
    if (len(selected) != count or len(set(selected)) != count or set(selected) - set(by_id)
            or len(browser_ids) != len(set(browser_ids)) or set(browser_ids) - set(selected)):
        raise ValueError('Driver accounts must be a disjoint subset of the full unique roster')
    if any(by_id[k]['initial_state'] != 'prepared' for k in selected):
        raise ValueError('v3 starts from the all-prepared immutable baseline')
    if mode == 'entry-mixed':
        roles = ['login'] * ((count + 2) // 3) + ['startup'] * ((count + 1) // 3) + ['save'] * (count // 3)
    elif 'mixed' in mode:
        other = {'login-mixed': 'login', 'startup-mixed': 'startup', 'submission-mixed': 'submission'}[mode]
        roles = ['save'] * (count // 3) + [other] * (count - count // 3)
    else:
        roles = [{'save-only': 'save'}.get(mode, mode)] * count
    roster = []
    for account_id, role in zip(selected, roles):
        account = by_id[account_id]
        questions = account['questions'][:(20 if workload == 'paper-20q' else 2)]
        if len(questions) != (20 if workload == 'paper-20q' else 2):
            raise ValueError('Frozen workload question bindings incomplete')
        schedule = []
        if role in ('save', 'full-flow', 'submission'):
            if workload == 'paper-20q':
                for qi, question in enumerate(questions):
                    a, b = values(question['question_type'])
                    for i in range(15):
                        offset = qi * 30000 + 1000 + i * 2000
                        sequence = [a if i % 2 == 0 else b]
                        if i == 0 and question['question_type'] in ('numeric', 'short_answer', 'fill_blank'):
                            sequence = [a, '', b, a]
                        for burst, value in enumerate(sequence):
                            schedule.append({'assignment_id': question['assignment_id'], 'value': copy.deepcopy(value),
                                             'offset_ms': offset + burst * 30})
            else:
                schedule = [{'assignment_id': questions[i % 2]['assignment_id'],
                             'value': copy.deepcopy(values(questions[i % 2]['question_type'])[(i // 2) % 2]),
                             'offset_ms': i * 2000} for i in range(15)]
        final = {a['assignment_id']: a['value'] for a in schedule}
        answers, prompted = [], 0
        for qi, q in enumerate(questions):
            # Assignment condition is frozen by the server fixture, never inferred
            # from observed successful confidence writes.
            condition = q.get('confidence_condition', q.get('confidence', {}).get('condition', 'not_assigned'))
            if condition == 'none':
                condition = 'not_assigned'
            probability = (0, 80, None)[prompted % 3] if condition == 'prompt' and q['assignment_id'] in final else None
            if condition == 'prompt':
                prompted += 1
            answers.append({**{k: q[k] for k in ('assignment_id', 'attempt_id', 'question_type')},
                            'value': final.get(q['assignment_id']), 'confidence_condition': condition,
                            'confidence_probability': probability})
        navigation, current = [], questions[0]['assignment_id']
        for action in schedule:
            if action['assignment_id'] != current:
                current = action['assignment_id']
                navigation.append(current)
        if current != questions[0]['assignment_id']:
            navigation.append(questions[0]['assignment_id'])
        roster.append({'account_id': account_id, 'user_id': account['user_id'], 'course_id': account['courseId'],
                       'task_id': account['taskId'], 'session_id': account['session_id'], 'role': role,
                       'driver': 'browser' if account_id in browser_ids else 'protocol',
                       'terminal_required': role in ('submission', 'full-flow') or (role == 'save' and workload == 'paper-20q'),
                       'answers': answers, 'save_schedule': schedule, 'navigation_schedule': navigation})
    if workload_profile == WORKLOAD_PROFILE:
        for index, row in enumerate(roster):
            row['login_offset_ms'] = 10000 * index / (count - 1)
    plan = {'version': VERSION, 'measurement_version': MEASUREMENT_VERSION,
            'metric_schema_version': METRIC_SCHEMA_VERSION, 'run_id': run_id, 'mode': mode, 'count': count, 'roster': roster,
            'source_fixture_id': fixture.get('source_fixture_id', fixture['run_id']),
            'baseline_snapshot_id': fixture['baseline_snapshot_id'], 'fixture_sha256': fixture_sha256,
            'fixture_spec_sha256': fixture['plan_sha'], 'target_origin': target, 'same_DUT': False,
            'transport': 'combined' if browser_ids else 'protocol', 'browser_operability_measured': False,
            'workload': workload, 'workload_duration_ms': 600000 if workload == 'paper-20q' else 30000,
            'preparation_login_concurrency': 4, 'save_interval_ms': 2000, 'save_debounce_ms': 100,
            'save_max_wait_ms': 250, 'operation_timeout_ms': 60000, 'login_timeout_ms': 30000,
            'submission_clock_boundary': 'confirmation-after-history-review-before-final-save-and-close',
            'completion_timeout_ms': 300000, 'maximum_release_skew_ms': 1000,
            'thresholds': {**v2.THRESHOLDS, 'navigation_p95_ms': 1000, 'navigation_p99_ms': 3000},
            'navigation_transport': 'save-and-leave-next-entry-v1',
            'baseline_protocol_version': v2.VERSION,
            'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    plan.update(workload_profile=workload_profile, login_window_ms=10000 if workload_profile == WORKLOAD_PROFILE else 0)
    if workload_profile == HISTORICAL_PROFILE:
        plan.update(measurement_version='measurement-v4r1', metric_schema_version='student-capacity-metrics-v2')
    return {**plan, 'plan_id': digest(plan)}


class LatestPointerQueue:
    """One in-flight request and one replaceable unsent pointer."""
    def __init__(self, debounce=.1, max_wait=.25):
        self.debounce, self.max_wait = debounce, max_wait
        self.pending, self.first, self.last, self.in_flight = None, None, None, False

    def put(self, pointer, *, now):
        if self.pending is None:
            self.first = now
        self.last, self.pending = now, pointer

    def take(self, *, now, force=False):
        if self.in_flight or self.pending is None:
            return None
        if not force and now + 1e-9 < min(self.last + self.debounce, self.first + self.max_wait):
            return None
        result, self.pending, self.in_flight = self.pending, None, True
        return result

    def complete(self):
        self.in_flight = False


class CapacityCapture(v2.CapacityCapture):
    def event(self, *args, **kwargs):
        row = super().event(*args, **kwargs)
        row['app_version'] = VERSION
        self.events[row['client_sequence_no']] = copy.deepcopy(row)
        return row

    def checkpoint(self, trigger):
        row = super().checkpoint(trigger)
        row['app_version'] = VERSION
        self.checkpoints[row['checkpoint_id']] = copy.deepcopy(row)
        return row


class Actor(v2.Actor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = CapacityCapture(self.account['session_id'], self.client_id)
        self.record.update(transport='protocol', same_DUT=False, driver='protocol', pointer_saves=[], navigation_samples=[])
        self.local = copy.deepcopy(self.expected)
        self.lock = threading.RLock()
        self.queue = LatestPointerQueue()
        self.queue_error, self.queue_stop = None, threading.Event()
        self.sender = None

    def _ack(self, bundle, receipt):
        super()._ack(bundle, receipt)
        acknowledged = {row['event_id'] for row in receipt.get('acknowledgements', [])}
        now = time.monotonic() * 1000
        calls = getattr(self.client, 'calls', [])
        sent = calls[-1].get('http_send_monotonic_ms') if calls else None
        for sample in self.record['save_samples']:
            if sample.get('event_id') in acknowledged and sample.get('event_ack_monotonic_ms') is None:
                sample['event_ack_monotonic_ms'] = now
                if sent is not None:
                    sample['capture_http_send_monotonic_ms'] = sent

    def install(self, dto, *, deadline):
        super().install(dto, deadline=deadline)
        contract = dto.get('pending_confidence')
        question = next((q for q in self.planned['answers'] if q['attempt_id'] == dto['assignment']['attempt_id']), None)
        if question is not None and bool(contract) != (question['confidence_condition'] == 'prompt'):
            raise ProtocolFailure('frozen_confidence_assignment_differs')
        if contract:
            self.stream.event(dto['assignment']['attempt_id'], 'confidence_observed', {
                'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()),
                'confidence_assignment_id': contract['confidence_assignment_id'], 'stage': 'presented',
                'answer_event_id': self.expected[dto['assignment']['attempt_id']]['target_event_id'],
                'probability': None, 'feedback_phase': 'before_feedback'}, event_version=2)

    def enter(self, assignment_id):
        if self.dto['assignment']['assignment_id'] == assignment_id:
            return
        began = time.monotonic() * 1000
        sample = {'assignment_id': assignment_id, 'started_monotonic_ms': began, 'status': 'failed'}
        self.record['navigation_samples'].append(sample)
        try:
            super().enter(assignment_id)
            sample.update(status='completed', ready_monotonic_ms=time.monotonic() * 1000)
        finally:
            sample['duration_ms'] = time.monotonic() * 1000 - began

    def login(self):
        began, retries = time.monotonic(), 0
        deadline = began + 30
        current_profile = self.plan.get('workload_profile') == WORKLOAD_PROFILE
        first_post = None
        try:
            if current_profile:
                if not self.record.get('anonymous_csrf_prepared') or not getattr(self.client, 'csrf', None):
                    raise ProtocolFailure('anonymous_csrf_not_prepared')
            else:
                self.csrf()
            while time.monotonic() < deadline:
                attempt = {'attempt': len(self.record['login_attempts']) + 1}
                self.record['login_attempts'].append(attempt)
                started = time.monotonic()
                if current_profile and first_post is None:
                    first_post = started
                    self.record['login_actual_monotonic_ms'] = started * 1000
                    planned = self.record.get('login_planned_monotonic_ms')
                    if planned is not None:
                        self.record['release_skew_ms'] = max(0, started * 1000 - planned)
                payload, status, retry = self.client.request('/api/v1/auth/login/',
                    {'username': self.account['username'], 'password': self.account['password']}, deadline=deadline)
                code = (payload.get('error') or {}).get('code') or payload.get('code')
                attempt.update(status=status, error_code=code, duration_ms=(time.monotonic() - started) * 1000,
                               elapsed_ms=(time.monotonic() - began) * 1000)
                if status == 200:
                    break
                if status != 429 or code != 'LOGIN_BUSY':
                    raise ProtocolFailure(str(code or f'http_{status}'))
                self.record['login_busy_responses'] += 1
                delay = (retry if retry is not None else 1) + random.uniform(0, min(2, .25 * 2 ** min(retries, 3)))
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
                retries += 1
            else:
                raise ProtocolFailure('login_timeout')
            checked(self.client, '/api/v1/student/courses/', deadline=deadline)
            tasks = checked(self.client, f'/api/v1/student/courses/{self.account["courseId"]}/tasks/', deadline=deadline)
            def contains(value):
                if isinstance(value, list):
                    return any(contains(v) for v in value)
                if isinstance(value, dict):
                    return any(value.get(k) == self.account['taskId'] for k in ('id', 'task_id')) or any(
                        contains(v) for v in value.values() if isinstance(v, (dict, list)))
                return False
            if not contains(tasks):
                raise ProtocolFailure('task_list_binding_missing')
            if time.monotonic() >= deadline:
                raise ProtocolFailure('login_timeout')
        finally:
            self.record['login_total_ms'] = (time.monotonic() - began) * 1000
            if current_profile and first_post is not None:
                self.record['login_ms'] = (time.monotonic() - first_post) * 1000

    def prepare(self, preparation_login_gate):
        role = self.planned['role']
        if self.plan.get('workload_profile') == WORKLOAD_PROFILE:
            with preparation_login_gate:
                self.csrf()
                self.record['anonymous_csrf_prepared'] = True
        if role in ('startup', 'save', 'submission'):
            with preparation_login_gate:
                self.login()
        if role in ('save', 'submission'):
            self.startup()
            self.manifest()
        if role == 'submission':
            # The identical frozen answer trajectory is setup for a submission
            # pressure cell; it is retained but excluded from measured saves.
            self.drive_schedule(time.monotonic(), paced=False)
            self.enter(self.planned['answers'][0]['assignment_id'])
            self.record['return_verified'] = True
            self.final_checkpoint()

    def _emit_action(self, index, action, released_at):
        sample = self.record['save_samples'][index]
        planned = (released_at + action['offset_ms'] / 1000) * 1000
        if self.dto['assignment']['assignment_id'] != action['assignment_id']:
            self.flush_pointer()
            self.enter(action['assignment_id'])
        with self.lock:
            aid = self.dto['assignment']['attempt_id']
            local = self.local[aid]
            event = self.stream.transitions(aid, self.dto['question']['question_type'], local['answer'],
                                            local['target_event_id'], [action['value']])[0]
            actual = time.monotonic() * 1000
            sample.update(status='emitted', planned_monotonic_ms=planned, action_start_monotonic_ms=actual,
                          actual_event_monotonic_ms=actual, event_id=event['event_id'],
                          client_backlog_ms=max(0, actual - planned), target_event_id=event['event_id'])
            local.update(answer=copy.deepcopy(action['value']), target_event_id=event['event_id'])
            local['transition_event_ids'].append(event['event_id'])
            confidence = next(q for q in self.planned['answers'] if q['attempt_id'] == aid)
            contract = self.dto.get('pending_confidence')
            if confidence['confidence_condition'] == 'prompt' and contract:
                probability = confidence['confidence_probability']
                self.stream.event(aid, 'confidence_observed', {'capture_version': 'capture-v2',
                    'operation_id': str(uuid.uuid4()), 'confidence_assignment_id': contract['confidence_assignment_id'],
                    'stage': 'skipped' if probability is None else 'responded', 'answer_event_id': event['event_id'],
                    'probability': probability, 'feedback_phase': 'before_feedback'}, event_version=2)
            self.queue.put({'attempt_id': aid, 'assignment_id': action['assignment_id'],
                            'answer': copy.deepcopy(action['value']), 'event': event}, now=time.monotonic())

    def _send_pointer(self, pointer):
        self.ensure_lease()
        with self.lock:
            expected = self.expected[pointer['attempt_id']]
            event = pointer['event']
            bundle = self._capture('ack_progress')
            planned_conf = next(q for q in self.planned['answers'] if q['attempt_id'] == pointer['attempt_id'])
            contract = self.dto.get('pending_confidence') or {}
            document = {'answer': pointer['answer'], 'target_event_id': event['event_id'],
                        'expected_version': expected['response_version'], 'client_occurred_at': event['client_wall_time_utc'],
                        **self.lease, 'capture_operation_id': str(uuid.uuid4()), 'capture_bundle': bundle}
            if planned_conf['confidence_condition'] == 'prompt':
                if not contract:
                    raise ProtocolFailure('frozen_confidence_assignment_differs')
                document['inline_confidence'] = {'probability': planned_conf['confidence_probability'],
                                                 'answer_event_id': event['event_id']}
            sent = time.monotonic() * 1000
            included = {e['event_id'] for e in bundle['events']}
            for sample in self.record['save_samples']:
                if sample.get('event_id') in included:
                    sample['http_send_monotonic_ms'] = sent
                    if sample.get('capture_http_send_monotonic_ms') is None:
                        sample['capture_http_send_monotonic_ms'] = sent
        # Keep the exact capture operation, event IDs and expected version when
        # delivery is unknown; replay resolves the existing idempotent receipt.
        self._pointer_intent = copy.deepcopy(document)
        try:
            receipt = checked(self.client, self.dto['actions']['save_draft_url'], document)
        except ProtocolFailure as error:
            if str(error) != 'transport_error':
                raise
            self.record.setdefault('unknown_delivery_replays', []).append({'capture_operation_id': document['capture_operation_id'],
                'target_event_id': event['event_id']})
            receipt = checked(self.client, self.dto['actions']['save_draft_url'], self._pointer_intent)
        acked = time.monotonic() * 1000
        with self.lock:
            if (receipt.get('committed') is not True or receipt.get('active_response_event_id') != event['event_id']
                    or receipt.get('response_version') != expected['response_version'] + 1):
                raise ProtocolFailure('save_head_not_acknowledged')
            self._ack(bundle, receipt)
            if 'inline_confidence' in document and (receipt.get('inline_confidence') or {}).get('probability') != planned_conf['confidence_probability']:
                raise ProtocolFailure('confidence_receipt_differs')
            expected.update(answer=copy.deepcopy(pointer['answer']), target_event_id=event['event_id'],
                            response_version=receipt['response_version'], confidence_probability=planned_conf['confidence_probability'])
            expected['transition_event_ids'].extend(e['event_id'] for e in bundle['events']
                if e['attempt_id'] == pointer['attempt_id'] and e['event_type'] == 'response_transition'
                and e['event_id'] not in expected['transition_event_ids'])
            self.record['pointer_saves'].append({'assignment_id': pointer['assignment_id'], 'target_event_id': event['event_id'],
                'response_version': receipt['response_version'], 'http_send_monotonic_ms': sent, 'ack_monotonic_ms': acked,
                'duration_ms': acked - sent, 'capture_event_ids': sorted(included)})
            ack_ids = {a['event_id'] for a in receipt.get('acknowledgements', [])}
            for sample in self.record['save_samples']:
                if sample.get('event_id') in ack_ids:
                    sample.update(status='acknowledged' if sample['event_id'] == event['event_id'] else 'superseded',
                        ack_monotonic_ms=acked,
                        pointer_target_event_id=event['event_id'], response_version=receipt['response_version'],
                        http_ms=acked - sent, duration_ms=acked - sent,
                        scheduled_ms=acked - sample['planned_monotonic_ms'])
            self._pointer_intent = None

    def _sender(self):
        try:
            while not self.queue_stop.is_set():
                with self.lock:
                    pointer = self.queue.take(now=time.monotonic())
                if pointer is None:
                    self.queue_stop.wait(.005)
                    continue
                self._send_pointer(pointer)
                with self.lock:
                    self.queue.complete()
        except Exception as error:
            self.queue_error = error

    def flush_pointer(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.queue_error:
                raise self.queue_error
            with self.lock:
                if self.queue.pending is None and not self.queue.in_flight:
                    return
            time.sleep(.005)
        raise ProtocolFailure('pointer_flush_timeout')

    def drive_schedule(self, released_at, *, paced=True):
        self.queue_stop.clear()
        self.sender = threading.Thread(target=self._sender, daemon=True)
        self.sender.start()
        try:
            for index, action in enumerate(self.planned['save_schedule']):
                if paced:
                    time.sleep(max(0, released_at + action['offset_ms'] / 1000 - time.monotonic()))
                if self.queue_error:
                    raise self.queue_error
                self._emit_action(index, action, released_at)
            if paced:
                time.sleep(max(0, released_at + self.plan['workload_duration_ms'] / 1000 - time.monotonic()))
            self.flush_pointer()
        finally:
            self.queue_stop.set()
            self.sender.join(timeout=65)
            if self.sender.is_alive():
                raise ProtocolFailure('pointer_sender_not_stopped')

    def run(self, released_at):
        role = self.planned['role']
        planned_at = released_at + self.planned.get('login_offset_ms', 0) / 1000
        time.sleep(max(0, planned_at - time.monotonic()))
        actual = time.monotonic()
        self.record.update(release_skew_ms=max(0, (actual - planned_at) * 1000),
            login_offset_ms=self.planned.get('login_offset_ms', 0), login_planned_monotonic_ms=planned_at * 1000,
            workload_profile=self.plan.get('workload_profile', HISTORICAL_PROFILE))
        if role in ('login', 'full-flow'):
            current_profile = self.plan.get('workload_profile') == WORKLOAD_PROFILE
            self.record['login_dispatch_monotonic_ms'] = actual * 1000
            if not current_profile:
                self.record['login_actual_monotonic_ms'] = actual * 1000
            try:
                self.login()
                self.record['login_ready_monotonic_ms'] = time.monotonic() * 1000
                self.record['login_planned_to_ready_ms'] = (time.monotonic() - planned_at) * 1000
            finally:
                if not current_profile:
                    self.record['login_ms'] = (time.monotonic() - actual) * 1000
        if role in ('startup', 'full-flow'):
            began = time.monotonic() if role == 'full-flow' else released_at
            self.startup()
            self.record['startup_ms'] = (time.monotonic() - began) * 1000
        if role == 'full-flow':
            self.manifest()
        if role in ('full-flow', 'save'):
            workload_start = time.monotonic() if role == 'full-flow' else released_at
            self.record['workload_start_monotonic_ms'] = workload_start * 1000
            self.drive_schedule(workload_start)
            review_started = time.monotonic()
            self.enter(self.planned['answers'][0]['assignment_id'])
            self.record['return_verified'] = True
            self.final_checkpoint()
            self.record['history_review_ms'] = (time.monotonic() - review_started) * 1000
        if self.planned['terminal_required']:
            self.submit()
            summary = checked(self.client, f'/api/v1/student/sessions/{self.account["session_id"]}/summary/')
            if summary.get('session_id') != self.account['session_id'] or summary.get('status') != 'completed':
                raise ProtocolFailure('finished_summary_not_completed')
            self.record['finished_read_verified'] = True
        elif role == 'startup':
            self.final_checkpoint()
        self.record['status'] = 'completed'

    def submit(self, *, clock=time.monotonic, sleep=time.sleep):
        clicked = clock()
        self.flush_pointer()
        began = clock()
        super().submit(clock=clock, sleep=sleep)
        closure_before_submit = max(0, (began - clicked) * 1000)
        self.record['submission_ms'] += closure_before_submit
        self.record['completion_ms'] += closure_before_submit
        self.record['submission_started_monotonic_ms'] = clicked * 1000
        self.record['processing_ms'] = self.record['completion_ms'] - self.record['submission_ms']

    def finish_record(self):
        known = {e['event_id'] for e in self.record['capture_events']}
        self.record['capture_events'].extend(copy.deepcopy(e) for e in self.stream.events.values() if e['event_id'] not in known)
        return super().finish_record()


def summarize(plan, records):
    # Preserve the full planned denominator, but never pool protocol and browser
    # durations into a percentile: two browser observations are descriptive only.
    protocol = [p for p in plan['roster'] if p.get('driver', 'protocol') == 'protocol']
    ids = {p['account_id'] for p in protocol}
    subplan = {**plan, 'count': len(protocol), 'roster': protocol}
    normalized = copy.deepcopy([r for r in records if r.get('account_id') in ids])
    report = v2.summarize(subplan, normalized)
    observed = {}
    for planned in protocol:
        rows = [row for row in normalized if row.get('account_id') == planned['account_id']]
        observed[planned['account_id']] = rows[0] if len(rows) == 1 and all(
            rows[0].get(key) == plan[key] for key in ('plan_id', 'run_id')) else {}
    samples = [(observed[p['account_id']].get('save_samples', [])[index]
                if index < len(observed[p['account_id']].get('save_samples', [])) else {})
               for p in protocol if p['role'] in ('save', 'full-flow')
               for index in range(len(p['save_schedule']))]
    def duration(sample, end, start):
        a, b = sample.get(end), sample.get(start)
        return a - b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    for name, end, start, definition in (
            ('save_scheduled', 'event_ack_monotonic_ms', 'planned_monotonic_ms', 'planned input to raw event durable acknowledgement'),
            ('save', 'event_ack_monotonic_ms', 'capture_http_send_monotonic_ms', 'raw event HTTP send to acknowledgement'),
            ('pointer_scheduled', 'ack_monotonic_ms', 'planned_monotonic_ms', 'planned input to durable answer pointer acknowledgement'),
            ('pointer_http', 'ack_monotonic_ms', 'http_send_monotonic_ms', 'answer pointer HTTP send to acknowledgement')):
        report[name] = metric_statistics([duration(sample, end, start) for sample in samples],
            limit=1000 if name == 'save_scheduled' else None,
            failed=sum(sample.get('status') == 'failed' for sample in samples), definition=definition)
    report['navigation'] = metric_statistics([r.get('navigation_samples', [])[index].get('duration_ms')
        if r and index < len(r.get('navigation_samples', [])) else None
        for p in protocol if p['role'] in ('save', 'full-flow')
        for index in range(len(p.get('navigation_schedule', [])))
        for r in [observed[p['account_id']]]], limit=1000, definition='navigation click to target ready')
    for metric, roles, limit in (('login', ('login', 'full-flow'), 10000),
                                 ('startup', ('startup', 'full-flow'), 5000)):
        report[metric] = metric_statistics([observed[p['account_id']].get(metric + '_ms')
            for p in protocol if p['role'] in roles], limit=limit)
    report['login_planned_to_ready'] = metric_statistics([observed[p['account_id']].get('login_planned_to_ready_ms')
        for p in protocol if p['role'] in ('login', 'full-flow')],
        definition='personal planned login click to usable task list; includes generator lateness')
    arrival_rows = [next((r for r in records if r.get('account_id') == p['account_id']
        and r.get('plan_id') == plan['plan_id'] and r.get('run_id') == plan['run_id']), {})
        for p in plan['roster'] if p['role'] in ('login', 'full-flow')]
    arrivals = [r.get('login_actual_monotonic_ms') for r in arrival_rows
                if isinstance(r.get('login_actual_monotonic_ms'), (int, float))]
    report['login_release_window'] = {'planned_window_ms': plan.get('login_window_ms', 0),
        'planned': len(arrival_rows), 'observed': len(arrivals), 'missing': len(arrival_rows) - len(arrivals),
        'first_actual_monotonic_ms': min(arrivals) if arrivals else None,
        'last_actual_monotonic_ms': max(arrivals) if arrivals else None,
        'actual_window_ms': max(arrivals) - min(arrivals) if arrivals else None}
    # v2 only measures explicit submission/full-flow roles. v3 paper save actors
    # also finish the whole paper and must remain in these metric denominators.
    for metric in ('submission', 'completion', 'processing'):
        report[metric] = metric_statistics([observed[p['account_id']].get(metric + '_ms')
            for p in protocol if p.get('terminal_required')],
            limit=1000 if metric == 'submission' else 60000 if metric == 'processing' else None)
    report['thresholds_met'] = True
    for metric in ('login', 'startup', 'save_scheduled', 'submission', 'navigation'):
        if report[metric]['planned']:
            report['thresholds_met'] &= report[metric]['missing'] == 0 and all(report[metric][q + '_ms'] is not None
                and report[metric][q + '_ms'] <= plan['thresholds'][metric + '_' + q + '_ms'] for q in ('p95', 'p99'))
    if report['processing']['planned']:
        report['thresholds_met'] &= (report['processing']['p95_ms'] is not None and report['processing']['p95_ms'] <= 60000
            and report['processing']['max_ms'] is not None and report['processing']['max_ms'] <= 300000)
    release = metric_statistics([observed[p['account_id']].get('release_skew_ms') for p in protocol], limit=1000)
    report['release_skew'] = release
    report['thresholds_met'] &= not protocol or release['max_ms'] is not None and release['max_ms'] <= plan['maximum_release_skew_ms']
    observed = Counter(r.get('account_id') for r in records)
    states = Counter(planned=plan['count'], completed=0, failed=0, missing=0, invalid=0)
    for p in plan['roster']:
        rows = [r for r in records if r.get('account_id') == p['account_id']]
        if not rows:
            state = 'missing'
        elif len(rows) != 1 or rows[0].get('plan_id') != plan['plan_id'] or rows[0].get('run_id') != plan['run_id']:
            state = 'invalid'
        else:
            state = rows[0].get('status') if rows[0].get('status') in ('completed', 'failed') else 'invalid'
        states[state] += 1
    states['invalid'] += sum(v for k, v in observed.items() if k not in {p['account_id'] for p in plan['roster']})
    report.update(version=VERSION, measurement_version=plan['measurement_version'], metric_schema_version=plan['metric_schema_version'],
        workload_profile=plan.get('workload_profile', HISTORICAL_PROFILE),
        scope='External protocol timings; browser observations reported individually.',
        counts=dict(states), protocol_counts={'planned': len(protocol),
        'observed': len(normalized)}, browser_observations=[r for r in records if r.get('account_id') not in ids],
        browser_planned_count=plan['count'] - len(protocol), same_DUT=False,
        quantile_population='protocol actors only; browser observations reported individually')
    report['thresholds_met'] &= states['completed'] == plan['count'] and not states['invalid']
    return report


def run(plan, fixture, output, *, ready_file=None, release_file=None):
    accounts = {a['account_id']: a for a in fixture['accounts']}
    planned_rows = [p for p in plan['roster'] if p.get('driver', 'protocol') == 'protocol']
    gate, release = threading.BoundedSemaphore(plan['preparation_login_concurrency']), threading.Event()
    prepared = [threading.Event() for _ in planned_rows]
    actors, records, state = {}, [], {}
    def worker(index, planned):
        actor = Actor(accounts[planned['account_id']], planned, plan, baseline.HttpClient(plan['target_origin']))
        actors[index] = actor
        stage = 'preparation'
        try:
            actor.prepare(gate)
            actor.record['preparation_ready'] = True
            prepared[index].set()
            if not release.wait(600):
                raise ProtocolFailure('shared_release_timeout')
            if state.get('error'):
                raise ProtocolFailure(state['error'])
            time.sleep(max(0, state['at'] - time.monotonic()))
            stage = 'workload'
            actor.run(state['at'])
        except Exception as error:
            actor.record.update(status='failed', failure_stage=stage,
                error_code=str(error) if isinstance(error, ProtocolFailure) else type(error).__name__)
        finally:
            prepared[index].set()
        return actor.finish_record()
    with ThreadPoolExecutor(max_workers=max(1, len(planned_rows)), thread_name_prefix='capacity-v3') as pool:
        futures = [pool.submit(worker, i, p) for i, p in enumerate(planned_rows)]
        deadline = time.monotonic() + 300
        for event in prepared:
            if not event.wait(max(0, deadline - time.monotonic())):
                state['error'] = 'preparation_timeout'
                break
        if not all(a.record.get('preparation_ready') for a in actors.values()):
            state['error'] = 'preparation_failed'
        if ready_file:
            with private_output(ready_file) as stream:
                json.dump({'plan_id': plan['plan_id'], 'run_id': plan['run_id'],
                    'ready': all(a.record.get('preparation_ready') for a in actors.values()),
                    'protocol_account_ids': [p['account_id'] for p in planned_rows],
                    'browser_account_ids': [p['account_id'] for p in plan['roster'] if p.get('driver') == 'browser'],
                    'preparation_failed_account_ids': [a.account['account_id'] for a in actors.values() if not a.record.get('preparation_ready')]}, stream)
        try:
            if release_file and not state.get('error'):
                while not Path(release_file).exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                release_document = json.loads(Path(release_file).read_text(encoding='utf-8-sig'))
                state['at'] = float(release_document['release_monotonic'])
                if state['at'] < time.monotonic() or state['at'] > time.monotonic() + 60:
                    raise ProtocolFailure('external_release_not_in_near_future')
            else:
                state['at'] = time.monotonic() + .2
        except (OSError, ValueError, KeyError, TypeError, ProtocolFailure) as error:
            state['error'] = str(error) if isinstance(error, ProtocolFailure) else 'external_release_invalid'
        finally:
            release.set()
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
            output.flush()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('fixture', 'mode', 'run-id', 'base-url', 'plan-output', 'output', 'summary'):
        parser.add_argument('--' + name, required=True, **({'choices': MODES} if name == 'mode' else {}))
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--workload', choices=WORKLOADS, default='paper-20q')
    parser.add_argument('--workload-profile', choices=(WORKLOAD_PROFILE, HISTORICAL_PROFILE), default=WORKLOAD_PROFILE)
    parser.add_argument('--actor-ids')
    parser.add_argument('--browser-account-ids', default='')
    parser.add_argument('--ready-file')
    parser.add_argument('--release-file')
    args = parser.parse_args()
    raw = Path(args.fixture).read_bytes()
    fixture = json.loads(raw.decode('utf-8-sig'))
    plan = build_plan(fixture, mode=args.mode, count=args.count, run_id=args.run_id,
        fixture_sha256=hashlib.sha256(raw).hexdigest(), base_url=args.base_url, workload=args.workload,
        workload_profile=args.workload_profile,
        actor_ids=args.actor_ids.split(',') if args.actor_ids else None,
        browser_account_ids=[a for a in args.browser_account_ids.split(',') if a])
    with private_output(args.plan_output) as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    with private_output(args.output) as output, private_output(args.summary) as summary:
        began = time.monotonic()
        records = run(plan, fixture, output, ready_file=args.ready_file, release_file=args.release_file)
        report = summarize(plan, records)
        report['generator_resources'] = {'wall_seconds': time.monotonic() - began, 'same_DUT': False}
        json.dump(report, summary, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'plan_id': plan['plan_id'], 'counts': report['counts'], 'thresholds_met': report['thresholds_met']}))
    return 0 if report['thresholds_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
