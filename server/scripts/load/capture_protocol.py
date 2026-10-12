"""HTTP capture-v2 workload. DOM/IME/IndexedDB correctness needs browser witnesses.

Only hashes, ownership, envelope fields and acknowledgement counts enter the ledger.
Queued/offline/reload operations here are protocol simulations, not browser evidence.
"""
import hashlib
import json
import os
import signal
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError


QUESTION_TYPES = ('single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer')
CONTRACT = 'school-capture-v2-protocol-v1'
BROWSER_ALIGNED_WORKLOAD = 'browser-aligned-v2'


class ShellAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if tag in ('script', 'link') and key in ('src', 'href') and value and value.startswith('/static/'):
                self.paths.add(value)


class _ErrorCodeOpener:
    """Retain only declared server codes when the HTTP runner discards the error body."""
    allowed_codes = frozenset(('invalid_event', 'invalid_event_link', 'invalid_event_order', 'invalid_request',
        'event_conflict', 'parent_not_received', 'response_hash_mismatch', 'response_parent_mismatch',
        'response_root_not_empty', 'unchanged_response', 'unknown_response_option', 'response_snapshot_mismatch',
        'capture_contract_mismatch', 'deadline_expired', 'task_terminated', 'course_access_revoked',
        'writer_lease_conflict', 'writer_lease_expired', 'navigation_response_version_conflict',
        'response_version_conflict', 'capture_mutation_material_conflict', 'invalid_bundle_link',
        'invalid_bundle_boundary', 'invalid_checkpoint', 'ENTRY_MATERIAL_CONFLICT', 'SESSION_NOT_ENTERABLE', 'CSRF_FAILED'))

    def __init__(self, opener, state, runner):
        self.opener, self.state, self.runner = opener, state, runner

    def open(self, *args, **kwargs):
        if hasattr(self.runner, 'remaining_budget'):
            remaining = self.runner.remaining_budget()
            if remaining is not None:
                kwargs['timeout'] = min(kwargs.get('timeout', self.runner.timeout), remaining)
        try:
            return self.opener.open(*args, **kwargs)
        except HTTPError as error:
            try:
                payload = json.loads(error.read(65536))
                code = payload.get('error', {}).get('code') if isinstance(payload, dict) else None
                self.state.browser_last_error_code = code if code in self.allowed_codes else 'unrecognized_error_code'
            except (ValueError, TypeError, AttributeError):
                self.state.browser_last_error_code = 'unreadable_error_code'
            raise


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def project_payload(value, fields, event_type):
    enrichment = {'before_snapshot_id', 'after_snapshot_id'} if event_type == 'response_transition' else (
        {'snapshot_id'} if event_type in ('research_tool_requested', 'research_tool_observed', 'confidence_observed') else set())
    if set(value) - set(fields) - enrichment:
        raise ValueError('Unexpected server payload enrichment')
    if set(fields) - set(value):
        raise ValueError('Missing client payload fields')
    return {key: value[key] for key in fields}


def answer_values(question):
    kind = question['question_type']
    if kind == 'true_false':
        return ['true', 'false', 'true', None, 'true']
    if kind in ('single_choice', 'multiple_choice'):
        keys = [row['key'] for row in question['options']]
        if len(keys) < 2:
            raise RuntimeError('Representative fixture requires two options')
        a, b = keys[:2]
        return [a, b, a, None, a] if kind == 'single_choice' else [[a], sorted([a, b]), [a], [], [a]]
    if kind in ('fill_blank', 'short_answer'):
        return ['A', 'AB', 'A', '', '填空答案' if kind == 'fill_blank' else '中文']
    raise RuntimeError('Unsupported protocol fixture question type')


def response_value(dto):
    kind = dto['question']['question_type']
    state = dto.get('initial_response_state')
    if isinstance(state, dict):
        value = dto['draft_answer']
        if state.get('state_hash') != digest({'question_type': kind, 'value': value}):
            raise RuntimeError('Restored response snapshot hash mismatch')
        return value
    if state is None:
        return [] if kind == 'multiple_choice' else ('' if kind in ('fill_blank', 'short_answer') else None)
    return state


class ProtocolStream:
    def __init__(self, session_id, client_instance_id=None):
        self.session_id = session_id
        self.client_instance_id = client_instance_id or str(uuid.uuid4())
        self.sequence = 0
        self.branches = {}
        self.origin = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
        self.started = time.monotonic()

    def event(self, attempt_id, event_type, payload):
        self.sequence += 1
        return {'event_id': str(uuid.uuid4()), 'event_type': event_type, 'event_version': 1,
            'session_id': self.session_id, 'attempt_id': attempt_id, 'client_sequence_no': self.sequence,
            'client_wall_time_utc': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()),
            'client_monotonic_ms': round((time.monotonic() - self.started) * 1000, 3),
            'client_time_origin': self.origin, 'time_sync_id': None, 'correlation_id': None,
            'quality_flags': [], 'app_version': CONTRACT, 'payload': payload}

    def transitions(self, attempt_id, kind, before, parent, values):
        rows = []
        branch = self.branches.setdefault(attempt_id, str(uuid.uuid4()))
        for value in values:
            before_state = {'question_type': kind, 'value': before}
            after_state = {'question_type': kind, 'value': value}
            event = self.event(attempt_id, 'response_transition', {'capture_version': 'capture-v2',
                'operation_id': str(uuid.uuid4()), 'branch_id': branch, 'parent_event_id': parent,
                'before_state': before_state, 'after_state': after_state,
                'before_state_hash': digest(before_state), 'after_state_hash': digest(after_state),
                'input_type': 'insertText' if kind in ('fill_blank', 'short_answer') else 'change', 'source': 'student'})
            rows.append(event)
            before, parent = value, event['event_id']
        return rows

    def composition_operations(self, attempt_id, parent):
        branch, operation = self.branches.setdefault(attempt_id, str(uuid.uuid4())), str(uuid.uuid4())
        return [self.event(attempt_id, 'input_operation', {'capture_version': 'capture-v2',
            'operation_id': operation, 'branch_id': branch, 'parent_event_id': parent,
            'input_type': 'insertCompositionText', 'composition': stage, 'selection_start': 0,
            'selection_end': 0}) for stage in ('started', 'updating', 'confirmed')]


class BrowserAlignedStream(ProtocolStream):
    """One session collector with checkpoints frozen from its observed ACKs."""
    def __init__(self, session_id, client_instance_id=None, *, started=None, origin=None):
        super().__init__(session_id, client_instance_id)
        if started is not None:
            self.started = started
        self.origin = origin or datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        self.events, self.event_ids, self.acked, self.checkpoints = {}, {}, set(), {}
        self.acked_checkpoint_ids = set()
        self.time_sync_id = None
        self.correlation_id = None

    def clock(self):
        return {'client_wall_time_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'client_monotonic_ms': round((time.monotonic() - self.started) * 1000, 3),
                'client_time_origin': self.origin}

    def event(self, attempt_id, event_type, payload, event_version=1):
        event = super().event(attempt_id, event_type, payload)
        event.update(self.clock(), app_version=BROWSER_ALIGNED_WORKLOAD,
                     time_sync_id=self.time_sync_id, event_version=event_version, correlation_id=self.correlation_id)
        self.events[event['client_sequence_no']] = deepcopy(event)
        self.event_ids[event['client_sequence_no']] = event['event_id']
        return event

    def accept_acknowledgements(self, acknowledgements):
        for ack in acknowledgements:
            event_id = self.event_ids.get(ack.get('client_sequence_no'))
            if event_id is None or event_id != ack.get('event_id'):
                raise RuntimeError('Unobserved event acknowledgement')
        self.acked.update(ack['client_sequence_no'] for ack in acknowledgements)
        for ack in acknowledgements:
            self.events.pop(ack['client_sequence_no'], None)

    def pending_events(self):
        return [deepcopy(event) for number, event in self.events.items() if number not in self.acked]

    def checkpoint(self, trigger):
        contiguous = 0
        while contiguous + 1 in self.acked:
            contiguous += 1
        document = {'checkpoint_id': str(uuid.uuid4()), 'session_id': self.session_id,
            'client_instance_id': self.client_instance_id, 'highest_emitted_sequence_no': self.sequence,
            'highest_acked_sequence_no': max(self.acked, default=0),
            'highest_contiguous_acked_sequence_no': contiguous, 'pending_event_count': len(self.pending_events()),
            'failed_event_count': 0, 'missing_local_sequence_ranges': [], **self.clock(),
            'time_sync_id': self.time_sync_id, 'trigger': trigger, 'quality_flags': [], 'app_version': BROWSER_ALIGNED_WORKLOAD}
        self.checkpoints[document['checkpoint_id']] = deepcopy(document)
        return document

    def frozen_checkpoint(self, checkpoint_id):
        return deepcopy(self.checkpoints[checkpoint_id])

    def validate_checkpoint(self, document):
        if self.checkpoints.get(document['checkpoint_id']) != document:
            raise RuntimeError('Checkpoint identity changed during replay')

    def answer_transition(self, attempt_id, kind, before, parent, value):
        branch = self.branches.setdefault(attempt_id, str(uuid.uuid4()))
        operation = str(uuid.uuid4())
        text = kind in ('fill_blank', 'short_answer')
        began = self.clock()['client_monotonic_ms']
        if text:
            self.event(attempt_id, 'input_operation', {'capture_version': 'capture-v2', 'operation_id': operation,
                'branch_id': branch, 'parent_event_id': parent, 'input_type': 'insertText', 'composition': 'none',
                'selection_start': 0, 'selection_end': 0})
        before_state, after_state = {'question_type': kind, 'value': before}, {'question_type': kind, 'value': value}
        event = self.event(attempt_id, 'response_transition', {'capture_version': 'capture-v2',
            'operation_id': operation, 'branch_id': branch, 'parent_event_id': parent,
            'before_state': before_state, 'after_state': after_state,
            'before_state_hash': digest(before_state), 'after_state_hash': digest(after_state),
            'input_type': 'insertText' if text else 'select', 'source': 'student',
            **({} if text else {'option_key': value[0] if isinstance(value, list) else value, 'option_action': 'select'})})
        if text:
            self.event(attempt_id, 'text_edit_observed', {'question_type': kind, 'edit_operation': 'insert',
                'content_hash_before': hashlib.sha256(before.encode('utf-8')).hexdigest(),
                'content_hash_after': hashlib.sha256(value.encode('utf-8')).hexdigest(),
                'content_length_before': len(before), 'content_length_after': len(value),
                'input_method': 'keyboard', 'batch_start_client_monotonic_ms': began,
                'batch_end_client_monotonic_ms': self.clock()['client_monotonic_ms'], 'is_ime': False}, event_version=2)
        return event


def emitted(ledger, body):
    for event in body.get('events', []):
        row = ledger.setdefault(str(event['session_id']), {'event_ids': [], 'events': {}, 'submission_tokens': [], 'answers': {}})
        expected = {'payload_sha256': digest(event['payload']), 'payload_fields': sorted(event['payload']),
            'client_instance_id': body['client_instance_id'], 'client_sequence_no': event['client_sequence_no'],
            'session_id': event['session_id'], 'attempt_id': event['attempt_id'], 'event_type': event['event_type'],
            'event_version': event['event_version']}
        if event.get('app_version') == BROWSER_ALIGNED_WORKLOAD:
            expected.update({key: event[key] for key in ('time_sync_id', 'client_wall_time_utc',
                'client_monotonic_ms', 'client_time_origin', 'app_version', 'correlation_id')})
        prior = row['events'].get(event['event_id'])
        if prior is not None and any(prior[key] != value for key, value in expected.items()):
            raise RuntimeError('Event identity changed during replay')
        if prior is None:
            prior = row['events'][event['event_id']] = {**expected, 'acknowledged': False,
                'emission_count': 0, 'ack_count': 0, 'server_ingest_sequence_no': None}
            row['event_ids'].append(event['event_id'])
        prior['emission_count'] += 1


def acknowledged(ledger, body, response):
    expected = {(event['event_id'], event['client_sequence_no']) for event in body['events']}
    acks = response.get('acknowledgements', [])
    actual = {(ack.get('event_id'), ack.get('client_sequence_no')) for ack in acks}
    if response.get('success') is not True or response.get('rejections') or actual != expected or len(acks) != len(expected):
        raise RuntimeError('Event acknowledgement mismatch')
    for ack in acks:
        event = next(event for event in body['events'] if event['event_id'] == ack['event_id'])
        row = ledger[str(event['session_id'])]['events'][ack['event_id']]
        ingest = ack.get('server_ingest_sequence_no')
        if type(ingest) is not int or ingest < 1 or row['server_ingest_sequence_no'] not in (None, ingest):
            raise RuntimeError('Event acknowledgement ingest mismatch')
    for ack in acks:
        event = next(event for event in body['events'] if event['event_id'] == ack['event_id'])
        row = ledger[str(event['session_id'])]['events'][ack['event_id']]
        row.update(acknowledged=True, server_ingest_sequence_no=ack['server_ingest_sequence_no'])
        row['ack_count'] += 1


@contextmanager
def evidence_files(manifest_path, output_path):
    """Reserve task and evidence before any HTTP; partial failures remain reviewable."""
    manifest_path, output_path = Path(manifest_path), Path(output_path)
    fixture = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    paths = {'result': output_path, 'attempts': output_path.with_suffix('.attempts.jsonl'),
             'ledger': manifest_path.with_suffix('.ledger.json')}
    claim = manifest_path.parent / f".school-task-{fixture['task_id']}.claim.json"
    for path in (*paths.values(), claim):
        if path.exists():
            raise FileExistsError(f'Evidence/task already claimed: {path.name}')
    with claim.open('x', encoding='utf-8') as handle:
        json.dump({'task_id': fixture['task_id'], 'run_id': fixture.get('run_id'),
                   'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                   'output_file': output_path.name, 'claimed_at': time.time()}, handle)
    with ExitStack() as stack:
        files = {key: stack.enter_context(path.open('x', encoding='utf-8')) for key, path in paths.items()}
        for path in paths.values():
            os.chmod(path, 0o600)
        def persist_partial(signum=None):
            runner = files.get('runner')
            if runner is not None:
                with runner.lock:
                    sessions = deepcopy(runner.ledger)
            else:
                sessions = {}
            if files['result'].tell() == 0:
                json.dump({'passed': False, 'incomplete': True, 'task_id': fixture['task_id'],
                           'reason': 'Workload did not produce a result; task remains claimed'}, files['result'])
            if files['ledger'].tell() == 0:
                json.dump({'task_id': fixture['task_id'], 'student_count': fixture.get('student_count'),
                    'source_config': fixture.get('source_config'), 'capture_protocol': fixture.get('capture_protocol'),
                    'incomplete': signum is not None or files.get('complete') is not True,
                    'interruption_signal': signum,
                    'workload_version': getattr(runner, 'workload_version', CONTRACT),
                    'sessions': sessions}, files['ledger'])
            for key in paths:
                files[key].flush()
                os.fsync(files[key].fileno())

        def interrupted(signum, frame):
            runner = files.get('runner')
            if runner is not None:
                runner.budget_deadline = time.monotonic()
                runner.budget_interrupted = True
            # Docker's bounded stop may kill us before HTTP threads unwind.
            persist_partial(signum)
            raise SystemExit(128 + signum)

        prior = signal.getsignal(signal.SIGTERM)
        main_thread = threading.current_thread() is threading.main_thread()
        if main_thread:
            signal.signal(signal.SIGTERM, interrupted)
        try:
            yield files
        finally:
            if main_thread:
                signal.signal(signal.SIGTERM, prior)
            persist_partial()


class CaptureProtocolMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ledger = {}
        self.protocol_scenarios = {}
        self.logical_actions, self.protocol_operation_measurements = [], []

    def request(self, state, path, *, method='GET', body=None):
        if getattr(self, 'workload_version', CONTRACT) == BROWSER_ALIGNED_WORKLOAD and hasattr(self, 'remaining_budget'):
            self.remaining_budget()
        bundle = (body.get('atomic_submission') or body.get('save_and_leave') or body.get('capture_bundle')) if body else None
        event_body = bundle or body
        checkpoints = (bundle.get('checkpoints', []) if bundle else
                       ([body] if body and 'checkpoint_id' in body else []))
        if state.session_id:
            with self.lock:
                self.ledger.setdefault(str(state.session_id), {'event_ids': [], 'events': {}, 'submission_tokens': [], 'answers': {}})
        if event_body and event_body.get('events'):
            with self.lock:
                emitted(self.ledger, event_body)
        if checkpoints:
            with self.lock:
                row = self.ledger[str(state.session_id)].setdefault('checkpoints', {})
                for checkpoint in checkpoints:
                    expected = {'payload_sha256': digest(checkpoint), 'client_instance_id': checkpoint['client_instance_id'],
                        'trigger': checkpoint['trigger'], 'highest_emitted_sequence_no': checkpoint['highest_emitted_sequence_no'],
                        'highest_acked_sequence_no': checkpoint['highest_acked_sequence_no'],
                        'highest_contiguous_acked_sequence_no': checkpoint['highest_contiguous_acked_sequence_no'],
                        'pending_event_count': checkpoint['pending_event_count']}
                    if getattr(self, 'workload_version', CONTRACT) == BROWSER_ALIGNED_WORKLOAD:
                        expected['document'] = deepcopy(checkpoint)
                    prior = row.setdefault(checkpoint['checkpoint_id'], {**expected, 'acknowledged': False, 'emission_count': 0})
                    if any(prior[key] != value for key, value in expected.items()):
                        raise RuntimeError('Checkpoint identity changed during replay')
                    prior['emission_count'] += 1
        if body and body.get('submission_token'):
            with self.lock:
                row = self.ledger.setdefault(str(state.session_id), {'event_ids': [], 'events': {}, 'submission_tokens': [], 'answers': {}})
                if body['submission_token'] not in row['submission_tokens']:
                    row['submission_tokens'].append(body['submission_token'])
        if body and body.get('save_and_leave'):
            left = next(event for event in bundle['events'] if event['event_id'] == body['leave_event_id'])
            expected = {'attempt_id': left['attempt_id'], 'to_assignment_id': body['to_assignment_id'],
                'target_event_id': body['target_event_id'], 'material_fingerprint': digest({key: body.get(key)
                    for key in ['save_and_leave', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at']})}
            with self.lock:
                receipts = self.ledger[str(state.session_id)].setdefault('navigation_receipts', {})
                old = receipts.setdefault(body['navigation_token'], {**expected, 'acknowledged': False})
                if any(old[key] != value for key, value in expected.items()):
                    raise RuntimeError('Navigation material changed during replay')
        opener = state.opener
        diagnostic = getattr(self, 'workload_version', CONTRACT) == BROWSER_ALIGNED_WORKLOAD
        if diagnostic:
            state.browser_last_error_code = None
            state.opener = _ErrorCodeOpener(opener, state, self)
        try:
            status, payload, seconds = super().request(state, path, method=method, body=body)
        finally:
            if diagnostic:
                state.opener = opener
        if event_body and event_body.get('events'):
            if status != 200:
                raise RuntimeError('Event batch failed')
            with self.lock:
                acknowledged(self.ledger, event_body, payload)
        if checkpoints:
            expected_ids = {checkpoint['checkpoint_id'] for checkpoint in checkpoints}
            actual_ids = payload.get('checkpoint_ids', []) if bundle else [payload.get('checkpoint_id')]
            if status not in (200, 201) or payload.get('success') is not True or set(actual_ids) != expected_ids or len(actual_ids) != len(expected_ids):
                raise RuntimeError('Checkpoint acknowledgement mismatch')
            with self.lock:
                for checkpoint_id in actual_ids:
                    self.ledger[str(state.session_id)]['checkpoints'][checkpoint_id]['acknowledged'] = True
        if body and body.get('save_and_leave'):
            with self.lock:
                self.ledger[str(state.session_id)]['navigation_receipts'][body['navigation_token']].update(
                    acknowledged=True, receipt_sha256=digest(payload))
        return status, payload, seconds

    def scenario(self, name):
        with self.lock:
            self.protocol_scenarios[name] = self.protocol_scenarios.get(name, 0) + 1

    def protocol_summary(self):
        events = [event for row in self.ledger.values() for event in row['events'].values()]
        result = {'contract': CONTRACT, 'evidence_kind': 'http_protocol_simulation',
            'browser_dom_ime_offline_verified': False, 'scenarios': self.protocol_scenarios,
            'unique_events': len(events), 'emissions': sum(row['emission_count'] for row in events),
            'acknowledgements': sum(row['ack_count'] for row in events),
            'all_events_acknowledged': bool(events) and all(row['acknowledged'] for row in events),
            'completed_sessions': sum(row.get('complete', False) for row in self.ledger.values())}
        result['workload_version'] = getattr(self, 'workload_version', CONTRACT)
        if result['workload_version'] == BROWSER_ALIGNED_WORKLOAD:
            checkpoints = [checkpoint for row in self.ledger.values() for checkpoint in row.get('checkpoints', {}).values()]
            result.update(logical_actions=self.logical_actions, checkpoints=len(checkpoints),
                all_checkpoints_acknowledged=bool(checkpoints) and all(row['acknowledged'] for row in checkpoints),
                operation_measurements=self.protocol_operation_measurements,
                browser_alignment_boundary='Frozen logical actions and HTTP facts, with a session collector and actual ten-second tools polling. Independent initial sync/auth/shell RPCs are serialized conservatively; DOM, IME, IndexedDB, background overlap and render readiness require browser witnesses.')
        return result

    def _post(self, state, path, body, metric):
        status, payload, seconds = self.request(state, path, method='POST', body=body)
        self.record(metric, seconds)
        if metric == 'finish' and status == 200 and isinstance(payload, bytes):
            return {}
        if status not in (200, 201) or not isinstance(payload, dict) or payload.get('success') is False:
            raise RuntimeError('Capture protocol mutation failed')
        return payload

    def _batch(self, state, stream, process, events):
        return self._post(state, process['event_batch_url'], {'client_instance_id': stream.client_instance_id,
            'events': events}, 'event_batch')

    def _lease(self, state, stream, process):
        lease = self._post(state, process['writer_lease_acquire_url'], {'client_instance_id': stream.client_instance_id}, 'lease_acquire')
        if not lease.get('lease_token') or lease.get('client_instance_id') != stream.client_instance_id:
            raise RuntimeError('Writer lease identity mismatch')
        return {'writer_lease_token': lease['lease_token'], 'writer_client_instance_id': stream.client_instance_id}

    def _release(self, state, process, lease):
        self._post(state, process['writer_lease_release_url'], {'lease_token': lease['writer_lease_token'],
                   'client_instance_id': lease['writer_client_instance_id']}, 'lease_release')

    def _save(self, state, dto, event, version, lease):
        result = self._post(state, dto['actions']['save_draft_url'], {'answer': event['payload']['after_state']['value'],
            'target_event_id': event['event_id'], 'expected_version': version,
            'client_occurred_at': event['client_wall_time_utc'], **lease}, 'save_draft')
        if result.get('active_response_event_id') != event['event_id'] or type(result.get('response_version')) is not int:
            raise RuntimeError('Saved target/version mismatch')
        return result['response_version']

    def _tools(self, state, stream, dto, target):
        endpoint = dto['actions'].get('research_tools_url')
        if not endpoint:
            raise RuntimeError('Representative fixture research tools missing')
        config = self._post(state, endpoint, {}, 'tools_configuration')
        attempt_id = dto['assignment']['attempt_id']
        process = dto['process']

        def action(tool, stage, data=None, initiation='self_initiated'):
            request = {'tool': tool, 'stage': stage, 'data': data or {}, 'initiation_type': initiation,
                'answer_event_id': target['event_id'], 'operation_id': str(uuid.uuid4())}
            event = stream.event(attempt_id, 'research_tool_requested', {'capture_version': 'capture-v2',
                'content_version': config['content_version'], **request})
            self._batch(state, stream, process, [event])
            result = self._post(state, endpoint, request, 'research_tool')
            if result.get('event_id') != request['operation_id'] or result.get('content_version') != config['content_version']:
                raise RuntimeError('Research tool reference mismatch')
            return result

        if config.get('doubt_enabled'):
            action('doubt', 'mark_added')
            action('doubt', 'mark_removed')
            self.scenario('doubt')
        if config.get('check_enabled'):
            action('check', 'mark_added')
            action('check', 'mark_removed')
            action('check', 'started')
            action('check', 'finished')
            self.scenario('check')
        for rule in config.get('rules', []):
            if rule['tool'] == 'check':
                initiation = 'task_required' if rule['required'] else 'system_prompted'
                action('check', 'started', {'rule_id': rule['id']}, initiation)
                action('check', 'finished', {'rule_id': rule['id']}, initiation)
                self.scenario('required_check')
        for hint in config.get('hints', [])[:1]:
            data = {'hint_id': hint['id']}
            source = action('hint', 'requested', data)
            for stage in ('presented', 'closed'):
                self._batch(state, stream, process, [stream.event(attempt_id, 'research_tool_observed', {
                    'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()), 'server_event_id': source['event_id'],
                    'tool': 'hint', 'stage': stage, 'data': data, 'initiation_type': 'self_initiated',
                    'answer_event_id': target['event_id'], 'content_version': source['content_version']})])
                if stage == 'presented':
                    action('hint', 'presented', data)
            self.scenario('hint_observed_protocol')

    def _feedback(self, state, stream, dto):
        question_id = dto['question']['snapshot_id']
        path = f"/api/v1/student/courses/{self.protocol_course_id}/learning/experiments/{self.protocol_experiment_id}/questions/{question_id}/"
        status, detail, seconds = self.request(state, path)
        self.record('feedback_detail', seconds)
        capture = detail.get('feedback_capture') if status == 200 else None
        if not capture or capture['authorized']['correct_answer'] is not True or capture['authorized']['explanation'] is not True:
            raise RuntimeError('Authorized full feedback capture missing')
        operation = str(uuid.uuid4())
        for stage in ('detail_entered', 'answer_presented', 'explanation_presented', 'detail_left'):
            event = stream.event(capture['attempt_id'], 'feedback_exposure', {'capture_version': 'capture-v2',
                'operation_id': operation, 'feedback_mode': capture['mode'], 'stage': stage,
                'answer_event_id': capture['answer_event_id'], 'snapshot_id': capture['snapshot_id'],
                'included_correctness': capture['authorized']['correctness'],
                'included_correct_answer': stage != 'detail_entered',
                'included_explanation': stage in ('explanation_presented', 'detail_left')})
            self._batch(state, stream, capture, [event])
        self.scenario('authorized_feedback_protocol')

    def _browser_request(self, state, path, metric, *, method='GET', body=None):
        began, status, error_type, successful = time.perf_counter(), None, None, False
        try:
            status, payload, _ = self.request(state, path, method=method, body=body)
            successful = status in (200, 201) and (not isinstance(payload, dict) or payload.get('success') is not False)
            if not successful:
                raise RuntimeError(f'HTTP {status}')
            return payload
        except Exception as error:
            error_type = type(error).__name__
            if str(error).startswith('HTTP ') and str(error)[5:].isdigit():
                status = int(str(error)[5:])
            raise
        finally:
            elapsed = time.perf_counter() - began
            self.record(metric, elapsed)
            with self.lock:
                self.protocol_operation_measurements.append({'metric': metric,
                    'student_index': getattr(state, 'load_student_index', None), 'elapsed_seconds': elapsed,
                    'http_status': status, 'error_type': error_type, 'successful': successful,
                    'server_error_code': getattr(state, 'browser_last_error_code', None) if not successful else None})

    def _browser_post(self, state, path, body, metric):
        return self._browser_request(state, path, metric, method='POST', body=deepcopy(body))

    def _browser_checkpoint(self, state, runtime, trigger):
        stream = runtime['stream']
        document = stream.checkpoint(trigger)
        stream.validate_checkpoint(document)
        if self._browser_bundle_enabled(runtime):
            return document
        self._browser_post(state, runtime['dto']['process']['checkpoint_url'], document, 'checkpoint')
        stream.acked_checkpoint_ids.add(document['checkpoint_id'])
        return document

    @staticmethod
    def _browser_bundle_enabled(runtime):
        capability = runtime['dto']['process'].get('capture_bundle', {})
        return capability.get('enabled') is True and capability.get('version') == 1

    def _browser_mutation_bundle_enabled(self, runtime):
        return self._browser_bundle_enabled(runtime) and runtime['dto']['process']['capture_bundle'].get('mutation_receipts') is True

    def _browser_capture_mutation(self, state, runtime, body, kind, result=None):
        token = body['capture_operation_id'] if kind == 'draft' else body['operation_id']
        expected = {'attempt_id': runtime['dto']['assignment']['attempt_id'], 'kind': kind,
                    'material_fingerprint': digest({key: value for key, value in body.items()
                        if key not in {'writer_lease_token', 'writer_client_instance_id'}}),
                    'event_identities': {str(row['client_sequence_no']): row['event_id'] for row in body['capture_bundle']['events']},
                    'checkpoint_ids': [row['checkpoint_id'] for row in body['capture_bundle']['checkpoints']],
                    'target_event_id': body.get('target_event_id') if kind == 'draft' else body.get('answer_event_id')}
        with self.lock:
            rows = self.ledger[str(state.session_id)].setdefault('capture_mutation_receipts', {})
            prior = rows.setdefault(token, {**expected, 'acknowledged': False, 'receipt_sha256': None})
            if any(prior[key] != value for key, value in expected.items()):
                raise RuntimeError('Capture mutation identity changed during replay')
            if result is not None:
                receipt_hash = digest(result)
                if prior['receipt_sha256'] not in (None, receipt_hash):
                    raise RuntimeError('Capture mutation receipt changed during replay')
                prior.update(acknowledged=True, receipt_sha256=receipt_hash)

    @staticmethod
    def _browser_freeze_bundle(runtime):
        stream = runtime['stream']
        bundle = {'version': 1, 'client_instance_id': stream.client_instance_id,
                  'event_boundary': stream.sequence, 'events': stream.pending_events(),
                  'checkpoints': [deepcopy(row) for key, row in stream.checkpoints.items()
                                  if key not in stream.acked_checkpoint_ids
                                  and row['highest_emitted_sequence_no'] <= stream.sequence]}
        if len(bundle['events']) > 100 or len(bundle['checkpoints']) > 100 or len(json.dumps(bundle).encode()) > 8 * 1024 * 1024:
            raise RuntimeError('Capture bundle exceeds the frozen bounded envelope')
        return bundle

    @staticmethod
    def _browser_accept_bundle(runtime, bundle, result):
        expected_events = {event['client_sequence_no']: event['event_id'] for event in bundle['events']}
        acks = result.get('acknowledgements')
        expected_checkpoints = {row['checkpoint_id'] for row in bundle['checkpoints']}
        ids = result.get('checkpoint_ids')
        if (result.get('committed') is not True or not isinstance(acks, list)
                or len(acks) != len(expected_events)
                or {row.get('client_sequence_no'): row.get('event_id') for row in acks} != expected_events
                or not isinstance(ids, list) or len(ids) != len(expected_checkpoints)
                or set(ids) != expected_checkpoints):
            raise RuntimeError('Capture bundle acknowledgement mismatch')
        runtime['stream'].accept_acknowledgements(acks)
        runtime['stream'].acked_checkpoint_ids.update(ids)

    def _browser_flush(self, state, runtime):
        stream = runtime['stream']
        events = stream.pending_events()
        if self._browser_bundle_enabled(runtime):
            bundle = self._browser_freeze_bundle(runtime)
            if not bundle['events'] and not bundle['checkpoints']:
                return
            response = self._browser_post(state, runtime['dto']['process']['event_batch_url'],
                {'capture_bundle': bundle}, 'event_batch')
            self._browser_accept_bundle(runtime, bundle, response)
            if events:
                self._browser_checkpoint(state, runtime, 'ack_progress')
                confirmation = self._browser_freeze_bundle(runtime)
                response = self._browser_post(state, runtime['dto']['process']['event_batch_url'],
                    {'capture_bundle': confirmation}, 'checkpoint_batch')
                self._browser_accept_bundle(runtime, confirmation, response)
            return
        if not events:
            return
        response = self._browser_post(state, runtime['dto']['process']['event_batch_url'],
            {'client_instance_id': stream.client_instance_id, 'events': events}, 'event_batch')
        stream.accept_acknowledgements(response['acknowledgements'])
        self._browser_checkpoint(state, runtime, 'ack_progress')

    def _browser_sync(self, state, runtime):
        stream, process = runtime['stream'], runtime['dto']['process']
        document = {'session_id': state.session_id, 'client_instance_id': stream.client_instance_id, **stream.clock()}
        created = self._browser_post(state, process['time_sync_create_url'], document, 'time_sync_create')
        with self.lock:
            samples = self.ledger[str(state.session_id)].setdefault('time_syncs', {})
            samples[created['time_sync_id']] = {'create': deepcopy(document), 'finalized': False}
        received = stream.clock()
        final_document = {
            'time_sync_id': created['time_sync_id'], 'client_instance_id': stream.client_instance_id,
            'client_received_wall_time_utc': received['client_wall_time_utc'],
            'client_received_monotonic_ms': received['client_monotonic_ms']}
        with self.lock:
            samples[created['time_sync_id']]['finalize'] = deepcopy(final_document)
        finalized = self._browser_post(state, process['time_sync_finalize_url'], final_document, 'time_sync_finalize')
        if finalized.get('sync_quality') != 'valid' or finalized.get('time_sync_id') != created['time_sync_id']:
            raise RuntimeError('Clock synchronization was not confirmed')
        stream.time_sync_id = finalized['time_sync_id']
        with self.lock:
            samples[created['time_sync_id']]['finalized'] = True

    def _browser_tool_action(self, state, runtime, tool, stage, data=None, rule=None):
        dto, stream = runtime['dto'], runtime['stream']
        initiation = ('task_required' if rule['required'] else 'system_prompted') if rule else 'self_initiated'
        request = {'tool': tool, 'stage': stage, 'data': {**(data or {}), **({'rule_id': rule['id']} if rule else {})},
            'initiation_type': initiation, 'answer_event_id': runtime['head'], 'operation_id': str(uuid.uuid4())}
        stream.event(dto['assignment']['attempt_id'], 'research_tool_requested', {
            'capture_version': 'capture-v2', 'content_version': runtime['tools']['content_version'], **request})
        bundle = self._browser_freeze_bundle(runtime) if self._browser_mutation_bundle_enabled(runtime) else None
        if bundle is not None:
            request['capture_bundle'] = bundle
            self._browser_capture_mutation(state, runtime, request, 'tool')
        else:
            self._browser_flush(state, runtime)
        response = self._browser_post(state, dto['actions']['research_tools_url'], request, 'research_tool')
        if bundle is not None:
            self._browser_accept_bundle(runtime, bundle, response)
            if bundle['events']:
                self._browser_checkpoint(state, runtime, 'ack_progress')
        if response.get('event_id') != request['operation_id'] or response.get('content_version') != runtime['tools']['content_version']:
            raise RuntimeError('Research tool reference mismatch')
        if bundle is not None:
            self._browser_capture_mutation(state, runtime, request, 'tool', response)
        return response

    def _browser_tool_observed(self, runtime, source, tool, stage, data, initiation):
        runtime['stream'].event(runtime['dto']['assignment']['attempt_id'], 'research_tool_observed', {
            'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()), 'server_event_id': source['event_id'],
            'tool': tool, 'stage': stage, 'data': data, 'initiation_type': initiation,
            'answer_event_id': runtime['head'], 'content_version': source['content_version']})

    def browser_open_question(self, state, assignment_id, reason='direct_open', dto=None):
        material = getattr(state, 'browser_entry_material', None) or self.entry_material(state)
        if dto is None:
            state.browser_entry_material = deepcopy(material)
            dto = self._browser_post(state, f'/api/v1/student/sessions/{state.session_id}/questions/{assignment_id}/', material, 'enter_item')
        receipt = dto.get('entry_receipt', {})
        process = dto['process']
        if getattr(self, 'require_student_slice_capabilities', False) and (
                process.get('save_and_leave', {}).get('version') != 1
                or process.get('atomic_submission', {}).get('version') != 1
                or process.get('atomic_submission', {}).get('enabled') is not True):
            raise RuntimeError('Student slice capabilities are missing from the frozen candidate')
        if (receipt.get('navigation_id') != material['navigation_id'] or receipt.get('session_id') != state.session_id
                or receipt.get('assignment_id') != assignment_id or receipt.get('attempt_id') != dto['assignment']['attempt_id']
                or any(not receipt.get(key) or receipt[key] != process.get(key) for key in ('visit_id', 'visit_index', 'entry_event_id'))):
            raise RuntimeError('Formal question entry receipt mismatch')
        state.browser_entry_material = None
        if dto['process'].get('capture_version') != 'capture-v2':
            raise RuntimeError('Required capture-v2 contract absent; legacy fallback forbidden')
        attempt_id, process = dto['assignment']['attempt_id'], dto['process']
        first_binding = not hasattr(state, 'browser_stream')
        if first_binding:
            state.browser_stream = BrowserAlignedStream(state.session_id, started=state.browser_page_started, origin=state.browser_page_origin)
        stream = state.browser_stream
        stream.correlation_id = process['navigation_id']
        with self.lock:
            self.ledger[str(state.session_id)].setdefault('visits', []).append({
                'attempt_id': attempt_id, 'assignment_id': assignment_id, 'client_instance_id': stream.client_instance_id,
                'entry_material': deepcopy(material), 'entry_receipt': deepcopy(receipt),
                **{key: process.get(key) for key in ('visit_id', 'visit_index', 'entry_event_id', 'navigation_id')}})
        runtime = {'dto': dto, 'stream': stream, 'head': process.get('active_response_event_id'), 'lease': {},
            'version': process['response_version'], 'answer': response_value(dto)}
        state.browser_current = runtime
        if first_binding:
            for event_type, payload in (('pageshow', {'persisted': False}), ('document_visible', {}), ('window_focused', {})):
                stream.event(attempt_id, event_type, payload)
            self._browser_checkpoint(state, runtime, 'resume')
        if first_binding or time.monotonic() >= state.browser_sync_due:
            self._browser_sync(state, runtime)
            state.browser_sync_due = time.monotonic() + 900
        self._browser_request(state, '/api/v1/auth/session/', 'offline_authorization')
        if first_binding:
            shell = self._browser_request(state, self.browser_shell_url, 'offline_shell')
            if not isinstance(shell, bytes):
                raise RuntimeError('Versioned browser shell is not HTML')
            assets = ShellAssets()
            assets.feed(shell.decode('utf-8'))
            for path in sorted(assets.paths):
                self._browser_request(state, path, 'offline_asset')
        stream.event(attempt_id, 'item_rendered', {'presentation_state': 'interactive'}, event_version=2)
        self._browser_flush(state, runtime)
        embedded_tools = dto.get('research_tools')
        runtime['tools'] = embedded_tools if isinstance(embedded_tools, dict) and isinstance(embedded_tools.get('rules'), list) else self._browser_post(
            state, dto['actions']['research_tools_url'], {}, 'tools_configuration')
        for rule in runtime['tools'].get('rules', []):
            if rule['tool'] != 'check':
                raise RuntimeError('Browser-aligned fixture requires only check prompt rules')
            source = rule.get('pending_request') or self._browser_tool_action(state, runtime, rule['tool'], 'requested', rule=rule)
            self._browser_tool_observed(runtime, source, rule['tool'], 'presented', {'rule_id': rule['id']},
                'task_required' if rule['required'] else 'system_prompted')
            if not rule.get('already_presented'):
                self._browser_tool_action(state, runtime, rule['tool'], 'presented', rule=rule)
        runtime['heartbeat_seconds'] = max(15, process.get('heartbeat_interval_seconds', 30))
        runtime['heartbeat_due'] = time.monotonic() + runtime['heartbeat_seconds']
        runtime['tools_refresh_due'] = time.monotonic() + 10
        runtime['last_interaction'] = time.monotonic()
        return runtime

    def _browser_ensure_lease(self, state, runtime):
        if runtime['lease'] and time.time() + 5 < runtime['lease_valid_until']:
            return
        stream, process = runtime['stream'], runtime['dto']['process']
        renew = bool(runtime['lease'])
        body = {'client_instance_id': stream.client_instance_id}
        if renew:
            body['lease_token'] = runtime['lease']['writer_lease_token']
        result = self._browser_post(state, process['writer_lease_renew_url' if renew else 'writer_lease_acquire_url'],
            body, 'lease_renew' if renew else 'lease_acquire')
        if not result.get('lease_token') or result.get('client_instance_id') != stream.client_instance_id:
            raise RuntimeError('Writer lease identity mismatch')
        runtime['lease'] = {'writer_lease_token': result['lease_token'], 'writer_client_instance_id': stream.client_instance_id}
        runtime['lease_valid_until'] = datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00')).timestamp()

    def _browser_save(self, state, runtime, event):
        bundle = self._browser_freeze_bundle(runtime) if self._browser_mutation_bundle_enabled(runtime) else None
        if bundle is None:
            self._browser_flush(state, runtime)
        self._browser_ensure_lease(state, runtime)
        body = {
            'answer': runtime['answer'], 'target_event_id': runtime['head'], 'expected_version': runtime['version'],
            'client_occurred_at': event['client_wall_time_utc'], **runtime['lease'],
            **({'capture_bundle': bundle, 'capture_operation_id': str(uuid.uuid4())} if bundle is not None else {})}
        if bundle is not None:
            self._browser_capture_mutation(state, runtime, body, 'draft')
        result = self._browser_post(state, runtime['dto']['actions']['save_draft_url'], body, 'save_draft')
        if bundle is not None:
            self._browser_accept_bundle(runtime, bundle, result)
            if bundle['events']:
                self._browser_checkpoint(state, runtime, 'ack_progress')
        if result.get('active_response_event_id') != runtime['head'] or type(result.get('response_version')) is not int:
            raise RuntimeError('Saved target/version mismatch')
        if bundle is not None:
            self._browser_capture_mutation(state, runtime, body, 'draft', result)
        runtime['version'] = result['response_version']

    def _browser_close(self, state, runtime, reason, to_assignment=None):
        if reason == 'navigation' and runtime['dto']['process'].get('save_and_leave', {}).get('version') == 1:
            stream = runtime['stream']
            material = state.browser_entry_material
            token = material['navigation_id']
            left = stream.event(runtime['dto']['assignment']['attempt_id'], 'item_left',
                {'leave_reason': reason, 'to_assignment_id': to_assignment}, event_version=2)
            left['correlation_id'] = token
            # The same immutable local fact must be used by journal, envelope and replay.
            stream.events[left['client_sequence_no']] = deepcopy(left)
            checkpoint = stream.checkpoint('navigation')
            checkpoints = self._browser_freeze_bundle(runtime)['checkpoints'] if self._browser_bundle_enabled(runtime) else [checkpoint]
            body = {'navigation_token': token, 'leave_event_id': left['event_id'],
                'to_assignment_id': to_assignment, 'target_event_id': runtime['head'],
                'client_clicked_at': material['client_occurred_at'], **runtime['lease'],
                'save_and_leave': {'version': 1, 'client_instance_id': stream.client_instance_id,
                    'event_boundary': stream.sequence, 'events': stream.pending_events(),
                    'checkpoints': checkpoints, 'expected_version': runtime['version']}}
            receipt = self._browser_post(state, runtime['dto']['actions']['save_draft_url'], body, 'save_and_leave')
            if (receipt.get('committed') is not True or receipt.get('navigation_token') != token
                    or receipt.get('to_assignment_id') != to_assignment
                    or receipt.get('active_response_event_id') != runtime['head']
                    or receipt.get('writer_lease_released') is not True):
                raise RuntimeError('Saved navigation receipt mismatch')
            stream.accept_acknowledgements(receipt['acknowledgements'])
            stream.acked_checkpoint_ids.update(receipt['checkpoint_ids'])
            runtime['version'] = receipt['response_version']
            return
        runtime['stream'].event(runtime['dto']['assignment']['attempt_id'], 'item_left',
            {'leave_reason': reason, 'to_assignment_id': to_assignment}, event_version=2)
        self._browser_checkpoint(state, runtime, 'navigation' if reason == 'navigation' else 'session_end')
        self._browser_flush(state, runtime)
        if runtime['lease']:
            self._browser_post(state, runtime['dto']['process']['writer_lease_release_url'], {
                'lease_token': runtime['lease']['writer_lease_token'], 'client_instance_id': runtime['stream'].client_instance_id}, 'lease_release')

    def _browser_logical_action(self, state, action, item_index):
        pending = getattr(state, 'browser_pending_action', None)
        if pending is not None:
            self._browser_finish_action(state, True)
        with self.lock:
            self.logical_actions.append({'student_index': getattr(state, 'load_student_index', None),
                'action': action, 'item_index': item_index})

    def _browser_start_action(self, state, action, item_index):
        state.browser_pending_action = {'action': action, 'item_index': item_index, 'began': time.perf_counter()}
        if action in ('navigation_next_ready', 'navigation_return_ready'):
            state.browser_entry_material = self.entry_material(state)

    def _browser_finish_action(self, state, successful, error=None):
        pending = getattr(state, 'browser_pending_action', None)
        if pending is None:
            return
        state.browser_pending_action = None
        seconds = time.perf_counter() - pending['began']
        self.record(pending['action'], seconds)
        finishing = pending['action'] == 'submit_next_ready' and pending['item_index'] == 19
        if finishing:
            self.record('finish_ready', seconds)
        with self.lock:
            measurement = {'metric': pending['action'],
                'student_index': getattr(state, 'load_student_index', None), 'item_index': pending['item_index'],
                'elapsed_seconds': seconds, 'http_status': None, 'successful': successful,
                'error_type': type(error).__name__ if error else None}
            self.protocol_operation_measurements.append(measurement)
            if finishing:
                self.protocol_operation_measurements.append({**measurement, 'metric': 'finish_ready'})

    def browser_before_item(self, state, index):
        now = time.monotonic()
        if not hasattr(self, 'browser_answer_origin'):
            state.browser_answer_origin = getattr(state, 'browser_answer_origin', now)
            state.browser_answer_wall_origin = getattr(state, 'browser_answer_wall_origin', time.time())
        origin = getattr(self, 'browser_answer_origin', getattr(state, 'browser_answer_origin', now))
        wall = getattr(self, 'browser_answer_wall_origin', getattr(state, 'browser_answer_wall_origin', time.time()))
        planned = origin + index * self.item_interval
        runtime = getattr(state, 'browser_current', None)
        while now < planned:
            wake_at = min(planned, runtime['heartbeat_due'], runtime.get('tools_refresh_due', float('inf'))) if runtime else planned
            remaining = self.remaining_budget() if hasattr(self, 'remaining_budget') else None
            if wake_at > now:
                time.sleep(min(wake_at - now, remaining) if remaining is not None else wake_at - now)
            if hasattr(self, 'remaining_budget'):
                self.remaining_budget()
            now = time.monotonic()
            if runtime and now >= runtime.get('tools_refresh_due', float('inf')):
                self._browser_post(state, runtime['dto']['actions']['research_tools_url'], {}, 'tools_refresh')
                runtime['tools_refresh_due'] = time.monotonic() + 10
                now = time.monotonic()
            if runtime and now >= runtime['heartbeat_due']:
                runtime['stream'].event(runtime['dto']['assignment']['attempt_id'], 'activity_heartbeat', {
                    'attempt_id': runtime['dto']['assignment']['attempt_id'], 'document_visible': True, 'window_focused': True,
                    'last_device_interaction_age_ms': max(0, round((now - runtime['last_interaction']) * 1000))})
                self._browser_checkpoint(state, runtime, 'heartbeat')
                self._browser_flush(state, runtime)
                runtime['heartbeat_due'] += runtime['heartbeat_seconds']
                now = time.monotonic()
        with self.lock:
            self.item_arrivals.append({'student_index': getattr(state, 'load_student_index', None), 'item_index': index,
                'planned_at': wall + index * self.item_interval, 'actual_at': time.time(),
                'planned_offset_seconds': index * self.item_interval, 'actual_offset_seconds': round(now - origin, 6),
                'generator_lateness_seconds': round(max(0, now - planned), 6), 'send_delay_seconds': round(max(0, now - planned), 6)})

    def browser_aligned_answer(self, state):
        runtime = getattr(state, 'browser_current', None) or self.browser_open_question(state, state.assignment_ids[0])
        assignments = [int(row['assignment_id']) for row in runtime['dto']['navigation']]
        if len(assignments) != 20 or len(set(assignments)) != 20:
            raise RuntimeError('Browser-aligned workload requires 20 assigned questions')
        for index, assignment_id in enumerate(assignments):
            self.browser_before_item(state, index)
            dto, stream = runtime['dto'], runtime['stream']
            if dto['assignment']['assignment_id'] != assignment_id:
                raise RuntimeError('Browser-aligned assigned order drifted')
            kind, attempt_id = dto['question']['question_type'], dto['assignment']['attempt_id']
            self._browser_start_action(state, 'answer_input', index)
            answer = [dto['question']['options'][0]['key']] if kind == 'multiple_choice' else (
                dto['question']['options'][0]['key'] if kind in ('single_choice', 'true_false') else '浏览器探针作答')
            stream.event(attempt_id, 'response_input_started', {'question_type': kind})
            target = stream.answer_transition(attempt_id, kind, runtime['answer'], runtime['head'], answer)
            runtime.update(head=target['event_id'], answer=answer, last_interaction=time.monotonic())
            self._browser_save(state, runtime, target)
            self._browser_logical_action(state, 'answer_input', index)
            if index < 19:
                self._browser_start_action(state, 'navigation_next_ready', index)
                self._browser_close(state, runtime, 'navigation', assignments[index + 1])
                runtime = self.browser_open_question(state, assignments[index + 1], 'navigation')
                self._browser_logical_action(state, 'navigation_next_ready', index)
                self._browser_start_action(state, 'navigation_return_ready', index)
                self._browser_close(state, runtime, 'navigation', assignment_id)
                runtime = self.browser_open_question(state, assignment_id, 'navigation')
                self._browser_logical_action(state, 'navigation_return_ready', index)
                dto, stream = runtime['dto'], runtime['stream']
            if kind == 'single_choice':
                rules = [rule for rule in runtime['tools']['rules'] if rule['tool'] == 'check' and rule['required']]
                if not rules:
                    raise RuntimeError('Frozen required check missing')
                for rule in rules:
                    for stage in ('started', 'finished'):
                        self._browser_tool_action(state, runtime, 'check', stage, rule=rule)
                self.scenario('required_check')
            if kind == 'fill_blank':
                hints = runtime['tools'].get('hints', [])
                if not hints:
                    raise RuntimeError('Frozen first hint missing')
                data = {'hint_id': hints[0]['id']}
                source = self._browser_tool_action(state, runtime, 'hint', 'requested', data)
                self._browser_tool_observed(runtime, source, 'hint', 'presented', data, 'self_initiated')
                self._browser_tool_action(state, runtime, 'hint', 'presented', data)
                self._browser_tool_observed(runtime, source, 'hint', 'closed', data, 'self_initiated')
                self.scenario('hint_observed_protocol')
            self._browser_start_action(state, 'submit_confirmation_ready', index)
            confidence, contract = None, dto.get('pending_confidence')
            if contract:
                for stage in ('presented', 'skipped'):
                    stream.event(attempt_id, 'confidence_observed', {'capture_version': 'capture-v2',
                        'operation_id': str(uuid.uuid4()), 'confidence_assignment_id': contract['confidence_assignment_id'],
                        'stage': stage, 'answer_event_id': runtime['head'], 'probability': None, 'feedback_phase': 'before_feedback'},
                        event_version=2 if contract['scale_version'] == 'v2' else 1)
                    if stage == 'presented':
                        self._browser_post(state, contract['prompt_shown_url'], {}, 'confidence_prompt')
                confidence = {'status': 'skipped', 'answer_event_id': runtime['head']}
                self.scenario('confidence_skip')
            self._browser_logical_action(state, 'submit_confirmation_ready', index)
            self._browser_start_action(state, 'submit_next_ready', index)
            token = str(uuid.uuid4())
            commit = stream.event(attempt_id, 'response_commit_requested', {'submission_token': token})
            self._browser_ensure_lease(state, runtime)
            body = {'submission_token': token, 'target_event_id': runtime['head'],
                'client_clicked_at': commit['client_wall_time_utc'], **runtime['lease']}
            if confidence:
                body['confidence'] = confidence
            capability = dto['process'].get('atomic_submission', {})
            atomic = getattr(self, 'atomic_submission', 'auto') != 'off' and capability.get('enabled') is True and capability.get('version') == 1
            if atomic:
                checkpoint = stream.checkpoint('commit')
                checkpoints = self._browser_freeze_bundle(runtime)['checkpoints'] if self._browser_bundle_enabled(runtime) else [checkpoint]
                body['atomic_submission'] = {'version': 1, 'client_instance_id': stream.client_instance_id,
                    'event_boundary': stream.sequence, 'events': stream.pending_events(), 'checkpoints': checkpoints,
                    'expected_version': runtime['version']}
            else:
                self._browser_save(state, runtime, commit)
                self._browser_checkpoint(state, runtime, 'commit')
            result = self._browser_post(state, dto['actions']['submit_url'], body, 'submit_item')
            if result.get('status') != 'submitted':
                raise RuntimeError('Submission was not confirmed')
            if atomic:
                if result.get('committed') is not True or result.get('final_response_event_id') != runtime['head']:
                    raise RuntimeError('Atomic submission receipt mismatch')
                stream.accept_acknowledgements(result['acknowledgements'])
                stream.acked_checkpoint_ids.update(result['checkpoint_ids'])
                self._browser_checkpoint(state, runtime, 'ack_progress')
            stream.event(attempt_id, 'feedback_shown', {'feedback_mode': result.get('feedback_mode', 'none'),
                'included_correctness': 'is_correct' in result, 'included_correct_answer': 'correct_answer' in result,
                'included_explanation': bool(result.get('explanation'))})
            if result.get('explanation'):
                stream.event(attempt_id, 'explanation_shown', {'source': 'submission_feedback'})
            self._browser_close(state, runtime, 'submit')
            with self.lock:
                row = self.ledger[str(state.session_id)]
                row['username_sha256'] = digest(state.username)
                row['answers'][str(attempt_id)] = {'state_sha256': target['payload']['after_state_hash'],
                    'target_event_id': runtime['head'], 'submission_token': token, 'question_type': kind,
                    'response_version': result.get('response_version', runtime['version'])}
            self.scenario(kind)
            next_path = urlsplit(result['next_url']).path
            if index < 19:
                expected = f'/student/sessions/{state.session_id}/questions/{assignments[index + 1]}/'
                if next_path != expected:
                    raise RuntimeError('Submission next question drifted')
                runtime = self.browser_open_question(state, assignments[index + 1], 'submit_next')
            else:
                self._browser_request(state, next_path, 'completion_shell')
                self._browser_request(state, '/api/v1/auth/session/', 'completion_auth')
                state.finish_payload = self._browser_request(state, f'/api/v1/student/sessions/{state.session_id}/summary/', 'finish')
                if (state.finish_payload.get('session_id') != state.session_id or state.finish_payload.get('status') != 'completed'
                        or state.finish_payload.get('submission_count') != 20 or state.finish_payload.get('attempt_count') != 20):
                    raise RuntimeError('Authoritative completion summary mismatch')
                recovery = self._browser_request(state, f'/api/v1/student/tasks/{self.task_id}/recovery/', 'task_recovery')
                if recovery.get('status') != 'completed':
                    raise RuntimeError('Terminal task completion was not confirmed')
            self._browser_logical_action(state, 'submit_next_ready', index)
        self.after_finish(state)
        with self.lock:
            self.ledger[str(state.session_id)]['complete'] = True

    def answer(self, state):
        if getattr(self, 'workload_version', CONTRACT) == BROWSER_ALIGNED_WORKLOAD:
            try:
                return self.browser_aligned_answer(state)
            except Exception as error:
                self._browser_finish_action(state, False, error)
                raise
        stream = ProtocolStream(state.session_id)
        visited, next_assignment, first_dto = set(), state.assignment_ids[0], None
        while next_assignment not in visited:
            item_started = time.monotonic()
            self.before_item(state, len(visited))
            visited.add(next_assignment)
            path = f'/api/v1/student/sessions/{state.session_id}/questions/{next_assignment}/'
            status, dto, seconds = self.request(state, path, method='POST', body=self.entry_material(state))
            self.record('enter_item', seconds)
            if status != 200 or dto['process'].get('capture_version') != 'capture-v2':
                raise RuntimeError('Required capture-v2 contract absent; legacy fallback forbidden')
            if dto['question']['question_type'] == 'single_choice':
                first_dto = first_dto or dto
            process, question = dto['process'], dto['question']
            kind, attempt_id = question['question_type'], dto['assignment']['attempt_id']
            if process.get('active_response_event_id') or process.get('response_version') != 0:
                raise RuntimeError('Protocol fixture task is not cold')
            lease = self._lease(state, stream, process)
            values = answer_values(question)
            empty = response_value(dto)
            prefix = stream.transitions(attempt_id, kind, empty, None, values[:4])
            self._batch(state, stream, process, prefix)
            if kind == 'short_answer':
                self._batch(state, stream, process, stream.composition_operations(attempt_id, prefix[-1]['event_id']))
                self.scenario('ime_protocol_simulation')
            final = stream.transitions(attempt_id, kind, values[3], prefix[-1]['event_id'], values[4:])[0]
            self._batch(state, stream, process, [final])
            version = self._save(state, dto, final, process['response_version'], lease)
            if kind == 'short_answer':
                self._release(state, process, lease)
                # Queue locally, refetch server DTO, then upload and replay the same IDs.
                queued = [stream.event(attempt_id, 'network_offline', {})]
                queued += stream.transitions(attempt_id, kind, values[-1], final['event_id'], ['离线甲', '离线甲乙'])
                status, restored, seconds = self.request(state, path)
                self.record('protocol_reload', seconds)
                if status != 200 or response_value(restored) != values[-1] or restored['process']['active_response_event_id'] != final['event_id'] or restored['process']['response_version'] != version:
                    raise RuntimeError('Reloaded pointer/state mismatch')
                stream.branches[attempt_id] = str(uuid.uuid4())
                queued += stream.transitions(attempt_id, kind, '离线甲乙', queued[-1]['event_id'], ['离线刷新后'])
                queued.append(stream.event(attempt_id, 'network_online', {}))
                lease = self._lease(state, stream, process)
                self._batch(state, stream, process, queued)
                self._batch(state, stream, process, queued)
                final = queued[-2]
                version = self._save(state, dto, final, version, lease)
                self.scenario('queued_replay_reload_protocol')
                self.scenario('writer_lease_reacquire')
            if kind in ('single_choice', 'fill_blank'):
                self._tools(state, stream, dto, final)
            token = str(uuid.uuid4())
            submitted = self._post(state, dto['actions']['submit_url'], {'submission_token': token,
                'target_event_id': final['event_id'], 'client_clicked_at': final['client_wall_time_utc'], **lease}, 'submit_item')
            if len(visited) < len(dto['navigation']) and any(key in submitted for key in ('correct_answer', 'explanation', 'is_correct')):
                raise RuntimeError('Feedback leaked during active attempt')
            self._release(state, process, lease)
            with self.lock:
                row = self.ledger[str(state.session_id)]
                row['username_sha256'] = digest(state.username)
                row['answers'][str(attempt_id)] = {'state_sha256': final['payload']['after_state_hash'],
                    'target_event_id': final['event_id'], 'submission_token': token, 'question_type': kind,
                    'response_version': version}
            self.scenario(kind)
            next_assignment = next((int(row['assignment_id']) for row in dto['navigation'] if int(row['assignment_id']) not in visited), next_assignment)
            self.after_item(state)
            remaining = self.item_interval - (time.monotonic() - item_started)
            if remaining > 0:
                time.sleep(remaining)
        state.finish_payload = self._post(state, dto['actions']['finish_url'], {}, 'finish')
        self._feedback(state, stream, first_dto)
        self.after_finish(state)
        with self.lock:
            self.ledger[str(state.session_id)]['complete'] = True
