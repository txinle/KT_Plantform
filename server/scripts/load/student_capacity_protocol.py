"""Browser-path-aligned HTTP capacity protocol; concurrency-v2 stays a control.

Synthetic protocol events do not establish actual browser render or human input.
"""
import argparse
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import student_concurrency_protocol as baseline
from student_concurrency_protocol import (HttpClient, ProtocolFailure, checked, digest,
    empty_value, origin, private_output, values)
from student_concurrency import _stats

VERSION = 'student-capacity-protocol-v2'
MODES = (*baseline.MODES, 'save-only', 'submission-mixed')
THRESHOLDS = {**baseline.THRESHOLDS, 'processing_p95_ms': 60000}


def select_roster(accounts, mode, count):
    if mode not in MODES or count not in (3, 20, 50, 180, 240, 360) or count > len(accounts):
        raise ValueError('Require an explicit bounded capacity denominator')
    if len(accounts) not in (180, 240, 360) or any(len({a[key] for a in accounts}) != len(accounts)
                                                for key in ('account_id', 'username', 'user_id', 'session_id')):
        raise ValueError('Require a unique isolated capacity cohort')
    if 'mixed' in mode:
        active = [a for a in accounts if a['initial_state'] == 'active']
        fresh = [a for a in accounts if a['initial_state'] == 'prepared']
        if len(active) != len(accounts) // 3 or len(fresh) != 2 * len(accounts) // 3:
            raise ValueError('Mixed cohort must preserve one-third active students')
        role = {'login-mixed': 'login', 'startup-mixed': 'startup', 'submission-mixed': 'submission'}[mode]
        return [(a, 'save') for a in active[:count // 3]] + [(a, role) for a in fresh[:count - count // 3]]
    if mode not in ('submission', 'save-only') and any(a['initial_state'] != 'prepared' for a in accounts):
        raise ValueError('Use a fresh prepared cohort')
    role = 'save' if mode == 'save-only' else ('submission' if mode == 'submission' else 'login')
    return [(a, role) for a in accounts[:count]]


def build_plan(fixture, *, mode, count, run_id, fixture_sha256, base_url):
    target = origin(base_url)
    if mode not in MODES:
        raise ValueError('Unknown protocol mode')
    if (fixture.get('isolated_demo') is not True or fixture.get('database') != 'kt_e2e'
            or fixture.get('source_ownership', {}).get('command') != 'seed_prepared_startup_load'
            or fixture.get('run_id') != run_id or digest(fixture['fixture_spec']) != fixture['plan_sha']):
        raise ValueError('Require the unchanged isolated fixture and matching run identity')
    selected = select_roster(fixture['accounts'], 'login' if mode == 'startup' else mode, count)
    roster = []
    for account, role in selected:
        if mode in ('startup', 'full-flow'):
            role = mode
        questions = account['questions'][:2]
        if len(questions) != 2:
            raise ValueError('Two frozen question bindings required')
        schedule = []
        if role == 'save':
            schedule = [{'assignment_id': questions[i % 2]['assignment_id'],
                         'value': copy.deepcopy(values(questions[i % 2]['question_type'])[(i // 2) % 2])}
                        for i in range(15)]
        elif role in ('full-flow', 'submission'):
            schedule = [{'assignment_id': questions[0]['assignment_id'], 'value': copy.deepcopy(values(questions[0]['question_type'])[0])},
                        {'assignment_id': questions[0]['assignment_id'], 'value': copy.deepcopy(values(questions[0]['question_type'])[1])},
                        {'assignment_id': questions[1]['assignment_id'], 'value': copy.deepcopy(values(questions[1]['question_type'])[0])}]
        final = {action['assignment_id']: action['value'] for action in schedule}
        roster.append({'account_id': account['account_id'], 'user_id': account['user_id'],
                       'course_id': account['courseId'], 'task_id': account['taskId'],
                       'session_id': account['session_id'], 'role': role,
                       'answers': [{'assignment_id': q['assignment_id'], 'attempt_id': q['attempt_id'],
                                    'question_type': q['question_type'], 'value': final.get(q['assignment_id']),
                                    'confidence_probability': None} for q in questions], 'save_schedule': schedule})
    plan = {'version': VERSION, 'run_id': run_id, 'mode': mode, 'count': count, 'roster': roster,
            'fixture_sha256': fixture_sha256, 'fixture_spec_sha256': fixture['plan_sha'],
            'target_origin': target, 'same_DUT': True, 'transport': 'protocol',
            'browser_operability_measured': False, 'preparation_login_concurrency': 4,
            'save_rounds': 15, 'save_interval_ms': 2000,
            'operation_timeout_ms': 60000, 'login_timeout_ms': 30000, 'completion_timeout_ms': 300000,
            'maximum_release_skew_ms': 1000, 'thresholds': THRESHOLDS,
            'navigation_transport': 'save-and-leave-next-entry-v1',
            'baseline_protocol_version': baseline.VERSION,
            'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return {**plan, 'plan_id': digest(plan)}


class CapacityCapture(baseline.ProtocolCapture):
    def transitions(self, attempt_id, kind, before, parent, values):
        if kind not in ('numeric', 'short_answer', 'fill_blank'):
            return super().transitions(attempt_id, kind, before, parent, values)
        rows = []
        branch = self.branches.setdefault(attempt_id, str(uuid.uuid4()))
        for value in values:
            operation = str(uuid.uuid4())
            previous, current = str(before) if before is not None else '', str(value) if value is not None else ''
            input_type = 'deleteContentBackward' if not current else 'insertText'
            began = self.clock()['client_monotonic_ms']
            self.event(attempt_id, 'input_operation', {'capture_version': 'capture-v2',
                'operation_id': operation, 'branch_id': branch, 'parent_event_id': parent,
                'input_type': input_type, 'composition': 'none', 'selection_start': 0,
                'selection_end': len(previous)})
            before_state, after_state = {'question_type': kind, 'value': before}, {'question_type': kind, 'value': value}
            event = self.event(attempt_id, 'response_transition', {'capture_version': 'capture-v2',
                'operation_id': operation, 'branch_id': branch, 'parent_event_id': parent,
                'before_state': before_state, 'after_state': after_state,
                'before_state_hash': digest(before_state), 'after_state_hash': digest(after_state),
                'input_type': input_type, 'source': 'student'})
            self.event(attempt_id, 'text_edit_observed', {'question_type': kind,
                'edit_operation': 'delete' if not current else 'replace',
                'content_hash_before': hashlib.sha256(previous.encode('utf-8')).hexdigest(),
                'content_hash_after': hashlib.sha256(current.encode('utf-8')).hexdigest(),
                'content_length_before': len(previous), 'content_length_after': len(current),
                'input_method': 'keyboard', 'batch_start_client_monotonic_ms': began,
                'batch_end_client_monotonic_ms': self.clock()['client_monotonic_ms'], 'is_ime': False}, event_version=2)
            rows.append(event)
            before, parent = value, event['event_id']
        return rows

    def event(self, *args, **kwargs):
        row = super().event(*args, **kwargs)
        row.update(app_version=VERSION, quality_flags=[] if self.time_sync_id else ['clock_unsynchronized'])
        self.events[row['client_sequence_no']] = copy.deepcopy(row)
        return row

    def checkpoint(self, trigger):
        row = super().checkpoint(trigger)
        row.update(app_version=VERSION, quality_flags=[] if self.time_sync_id else ['clock_unsynchronized'])
        self.checkpoints[row['checkpoint_id']] = copy.deepcopy(row)
        return row


class Actor(baseline.Actor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = CapacityCapture(self.account['session_id'], self.client_id)
        self.record['time_syncs'] = []
        self._content_cache = {}
        self._navigation_intent = None

    def ensure_lease(self):
        expires = datetime.fromisoformat(self.lease_expires_at.replace('Z', '+00:00')) if self.lease_expires_at else None
        now = datetime.now(timezone.utc)
        if self.lease and expires and (expires - now).total_seconds() > 5:
            return
        payload = None
        if self.lease and expires and expires > now:
            payload, status, _ = self.client.request(self.dto['process']['writer_lease_renew_url'],
                {'client_instance_id': self.client_id, 'lease_token': self.lease['writer_lease_token']})
            if status != 200:
                code = (payload.get('error') or {}).get('code') or payload.get('code') or f'http_{status}'
                if status != 409 or code != 'writer_lease_conflict':
                    raise ProtocolFailure(str(code))
                self.lease, self.lease_expires_at, payload = None, None, None
        if payload is None:
            payload = checked(self.client, self.dto['process']['writer_lease_acquire_url'],
                {'client_instance_id': self.client_id}, allowed=(200, 201))
        self.lease = {'writer_lease_token': payload['lease_token'], 'writer_client_instance_id': self.client_id}
        self.lease_expires_at = payload['expires_at']
        self.lease_fact('acquisitions')

    def install(self, dto, *, deadline):
        super().install(dto, deadline=deadline)
        process = self.dto['process']
        self.stream.correlation_id = process.get('navigation_id')
        if self.stream.time_sync_id is None:
            document = {'session_id': self.account['session_id'], 'client_instance_id': self.client_id,
                        **self.stream.clock()}
            created = checked(self.client, process['time_sync_create_url'], document, allowed=(200, 201), deadline=deadline)
            received = self.stream.clock()
            final = {'time_sync_id': created['time_sync_id'], 'client_instance_id': self.client_id,
                     'client_received_wall_time_utc': received['client_wall_time_utc'],
                     'client_received_monotonic_ms': received['client_monotonic_ms']}
            confirmed = checked(self.client, process['time_sync_finalize_url'], final, deadline=deadline)
            if confirmed.get('sync_quality') != 'valid' or confirmed.get('time_sync_id') != created['time_sync_id']:
                raise ProtocolFailure('time_sync_not_confirmed')
            self.stream.time_sync_id = confirmed['time_sync_id']
            self.record['time_syncs'].append({'create': document, 'finalize': final, 'confirmed': confirmed})
            # Declared synthetic page state follows the browser lifecycle contract.
            # It never establishes an actual browser render or device activity.
            for event_type, payload in (('pageshow', {'persisted': False}),
                                        ('document_visible', {}), ('window_focused', {})):
                self.stream.event(self.dto['assignment']['attempt_id'], event_type, payload)
        self.stream.event(self.dto['assignment']['attempt_id'], 'item_rendered',
                          {'presentation_state': 'interactive'}, event_version=3)

    def content(self, descriptor, deadline):
        key = (descriptor['url'], descriptor['sha256'], descriptor['key'])
        if key not in self._content_cache:
            self._content_cache[key] = super().content(descriptor, deadline)
        return copy.deepcopy(self._content_cache[key])

    def _capture(self, trigger):
        checkpoint = self.stream.checkpoint(trigger)
        events = self.stream.pending_events()
        known = {row['event_id'] for row in self.record['capture_events']}
        self.record['capture_events'].extend(copy.deepcopy(row) for row in events if row['event_id'] not in known)
        self.record['capture_checkpoints'].append(copy.deepcopy(checkpoint))
        return {'version': 1, 'client_instance_id': self.client_id,
                'event_boundary': self.stream.sequence, 'events': events, 'checkpoints': [checkpoint]}

    def _ack(self, bundle, receipt):
        acknowledgements = receipt.get('acknowledgements', [])
        self.stream.accept_acknowledgements(acknowledgements)
        ids = receipt.get('checkpoint_ids', [])
        if any(row['client_sequence_no'] not in self.stream.acked for row in bundle['events']) or any(
                row['checkpoint_id'] not in ids for row in bundle['checkpoints']):
            raise ProtocolFailure('navigation_capture_not_acknowledged')
        self.record['event_acknowledgements'].extend(copy.deepcopy(acknowledgements))
        self.record['checkpoint_acknowledgements'].extend(ids)

    def enter(self, assignment_id):
        if self.dto['assignment'].get('assignment_id') == assignment_id:
            return
        process = self.dto['process']
        if (process.get('save_and_leave', {}).get('version') != 1
                or process.get('transition_entry', {}).get('enabled') is not True):
            raise ProtocolFailure('browser_navigation_capability_missing')
        if self._navigation_intent is None:
            expires = datetime.fromisoformat(self.lease_expires_at.replace('Z', '+00:00')) if self.lease_expires_at else None
            credentials = self.lease if self.lease and expires and expires > datetime.now(timezone.utc) else {}
            material = baseline.new_navigation_material()
            material['client_instance_id'] = self.client_id
            material.update(assignment_id=assignment_id, reason='navigation_click')
            token = material['navigation_id']
            self.stream.correlation_id = token
            left = self.stream.event(self.dto['assignment']['attempt_id'], 'item_left',
                {'leave_reason': 'navigation', 'to_assignment_id': assignment_id}, event_version=2)
            bundle = self._capture('navigation')
            expected = self.expected[self.dto['assignment']['attempt_id']]
            body = {'navigation_token': token, 'leave_event_id': left['event_id'],
                    'target_event_id': expected['target_event_id'], 'to_assignment_id': assignment_id,
                    'client_clicked_at': material['client_occurred_at'], **credentials,
                    'save_and_leave': {**bundle, 'expected_version': expected['response_version']},
                    'next_entry': material}
            self._navigation_intent = (self.dto['actions']['save_draft_url'], body, expected)
        path, body, expected = self._navigation_intent
        if body['to_assignment_id'] != assignment_id:
            raise ProtocolFailure('previous_navigation_unconfirmed')
        receipt = checked(self.client, path, body)
        if (receipt.get('committed') is not True or receipt.get('writer_lease_released') is not True
                or receipt.get('navigation_token') != body['navigation_token']
                or receipt.get('active_response_event_id') != expected['target_event_id']
                or receipt.get('response_version', -1) < expected['response_version']):
            raise ProtocolFailure('navigation_receipt_binding_mismatch')
        self._ack(body['save_and_leave'], receipt)
        expected['response_version'] = receipt['response_version']
        outcome = receipt.get('next_entry', {})
        if outcome.get('committed') is not True:
            # Retain the exact original material; a later enter() replays it.
            raise ProtocolFailure('target_entry_unconfirmed')
        # The navigation receipt confirms release, not the internally acquired
        # token/expiry when the browser omitted an expired credential.
        self.lease, self.lease_expires_at = None, None
        self.lease_fact('releases')
        self.record['lease_releases'][-1].update(confirmation_source='navigation_receipt',
            navigation_token=receipt['navigation_token'])
        self.install(outcome['runtime'], deadline=time.monotonic() + 60)
        self._navigation_intent = None

    def final_checkpoint(self):
        # The browser performs final material closure inside the measured click.
        if self.planned['role'] not in ('submission', 'full-flow'):
            if self.stream.pending_events():
                bundle = self._capture('ack_progress')
                receipt = checked(self.client, self.dto['process']['event_batch_url'], {'capture_bundle': bundle}, allowed=(200, 201))
                self._ack(bundle, receipt)
            super().final_checkpoint()

    def run(self, released_at):
        super().run(released_at)
        if self.dto and self.planned['role'] == 'startup':
            self.final_checkpoint()

    def submit(self, *, clock=time.monotonic, sleep=time.sleep):
        began = clock()
        self.ensure_lease()
        self.stream.correlation_id = self.dto['process'].get('navigation_id')
        left = self.stream.event(self.dto['assignment']['attempt_id'], 'item_left',
            {'leave_reason': 'finish', 'to_assignment_id': None}, event_version=2)
        bundle = self._capture('session_end')
        operation = str(uuid.uuid4())
        receipt = checked(self.client, self.dto['process']['writer_lease_release_url'], {
            'lease_token': self.lease['writer_lease_token'], 'client_instance_id': self.client_id,
            'capture_operation_id': operation, 'leave_event_id': left['event_id'], 'capture_bundle': bundle})
        if receipt.get('capture_operation_id') != operation or receipt.get('writer_lease_released') is not True:
            raise ProtocolFailure('final_close_not_confirmed')
        self._ack(bundle, receipt)
        self.lease_fact('releases')
        self.lease, self.lease_expires_at = None, None
        baseline.Actor.final_checkpoint(self)
        before_request = clock()
        super().submit(clock=clock, sleep=sleep)
        preparation_ms = (before_request - began) * 1000
        self.record['submission_ms'] += preparation_ms
        self.record['completion_ms'] += preparation_ms
        # Completion polling bounds include polling lag and remain conservative.
        self.record['processing_ms'] = self.record['completion_ms'] - self.record['submission_ms']


def summarize(plan, records):
    report = baseline.summarize(plan, records)
    observed = {row['account_id']: row for row in records}
    report['version'] = VERSION
    report['processing'] = _stats([observed.get(row['account_id'], {}).get('processing_ms')
        for row in plan['roster'] if row['role'] in ('submission', 'full-flow')])
    if report['processing']['planned']:
        report['thresholds_met'] &= (report['processing']['p95_ms'] is not None
            and report['processing']['p95_ms'] <= 60000 and report['processing']['max_ms'] is not None
            and report['processing']['max_ms'] <= 300000)
    return report


def run(plan, fixture, output):
    accounts = {row['account_id']: row for row in fixture['accounts']}
    preparation_login_gate = threading.BoundedSemaphore(plan['preparation_login_concurrency'])
    release, release_state = threading.Event(), {}
    ready = [threading.Event() for _ in plan['roster']]
    records = []
    def worker(index, planned):
        actor = Actor(accounts[planned['account_id']], planned, plan, HttpClient(plan['target_origin']))
        stage = 'preparation'
        try:
            actor.prepare(preparation_login_gate)
            ready[index].set()
            if not release.wait(180):
                raise ProtocolFailure('shared_release_timeout')
            stage = 'workload'
            actor.run(release_state['at'])
        except Exception as error:
            actor.record.update(status='failed', error_code=str(error) if isinstance(error, ProtocolFailure) else type(error).__name__, failure_stage=stage)
        finally:
            ready[index].set()
        return actor.finish_record()
    with ThreadPoolExecutor(max_workers=plan['count'], thread_name_prefix='student-protocol') as pool:
        futures = [pool.submit(worker, i, row) for i, row in enumerate(plan['roster'])]
        prep_deadline = time.monotonic() + 180
        for event in ready:
            event.wait(max(0, prep_deadline - time.monotonic()))
        release_state['at'] = time.monotonic()
        release.set()
        for future in as_completed(futures):
            row = future.result()
            records.append(row)
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            output.flush()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--count', type=int, choices=(3, 20, 50, 180, 240, 360), required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--plan-output', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--summary', required=True)
    args = parser.parse_args()
    raw = Path(args.fixture).read_bytes()
    fixture = json.loads(raw.decode('utf-8-sig'))
    plan = build_plan(fixture, mode=args.mode, count=args.count, run_id=args.run_id,
                      fixture_sha256=hashlib.sha256(raw).hexdigest(), base_url=args.base_url)
    with private_output(args.plan_output) as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
    # Reserve every artifact before any HTTP side effect.
    with private_output(args.output) as output, private_output(args.summary) as summary:
        try:
            import resource
            initial = resource.getrusage(resource.RUSAGE_SELF)
        except ImportError:
            resource, initial = None, None
        began = time.monotonic()
        records = run(plan, fixture, output)
        report = summarize(plan, records)
        final = resource.getrusage(resource.RUSAGE_SELF) if resource else None
        report['generator_resources'] = {'wall_seconds': time.monotonic() - began,
            'user_cpu_seconds': final.ru_utime - initial.ru_utime if final else None,
            'system_cpu_seconds': final.ru_stime - initial.ru_stime if final else None,
            'peak_rss_bytes': final.ru_maxrss * 1024 if final and os.name == 'posix' else None,
            'source': 'Linux resource.getrusage(RUSAGE_SELF)' if final else 'unavailable', 'same_DUT': True}
        json.dump(report, summary, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'plan_id': plan['plan_id'], 'counts': report['counts'], 'thresholds_met': report['thresholds_met']}, allow_nan=False))
    return 0 if report['thresholds_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
