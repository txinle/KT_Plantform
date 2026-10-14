"""Reconcile passive browser network observations against isolated durable facts."""
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.assessment.models import (AssessmentEvent, Attempt, CaptureMutationReceipt, ClientCaptureCheckpoint, ConfidenceAssignment,
    ExperimentSession, NavigationReceipt, QuestionVisitEvent, SubmissionEvent, StudentQuestionAssignment)
from apps.experiments.models import ClassExperimentTask
from scripts.load.capture_protocol import project_payload
from scripts.load.natural_workload import confidence_decision

HASH = re.compile(r'^[a-f0-9]{64}$')
ENTRY_PATH = re.compile(r'^/api/v1/student/sessions/(\d+)/questions/(\d+)/$')
ATTEMPT_PATH = re.compile(r'^/api/v1/student/attempts/(\d+)/(?:draft|submit|skip|recover)/$')
PROTOCOL_PATH = re.compile(r'^/api/v1/(?:assessment/process/[a-z0-9/-]+/|student/attempts/\d+/(?:draft|submit|skip|recover)/|student/sessions/\d+/questions/\d+/)$')
CANDIDATE_FIELDS = ('source_sha256', 'frontend_sha256', 'config_sha256')
CHECKPOINT_FIELDS = {'checkpoint_id', 'session_id', 'client_instance_id', 'highest_emitted_sequence_no',
    'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no', 'pending_event_count',
    'failed_event_count', 'missing_local_sequence_ranges', 'client_monotonic_ms', 'client_wall_time_utc',
    'client_time_origin', 'time_sync_id', 'trigger', 'quality_flags', 'app_version'}
NAVIGATION_RECEIPT_FIELDS = ('success', 'committed', 'navigation_token', 'acknowledgements', 'checkpoint_ids',
    'response_version', 'active_response_event_id', 'saved_at', 'status', 'writer_lease_released', 'to_assignment_id', 'next_url')
SUBMISSION_RECEIPT_FIELDS = ('committed', 'acknowledgements', 'checkpoint_ids', 'final_response_event_id', 'response_version')
CLOSE_RECEIPT_FIELDS = ('success', 'committed', 'capture_operation_id', 'acknowledgements', 'checkpoint_ids',
    'writer_lease_released', 'status', 'response_version', 'active_response_event_id', 'final_response_event_id')
CLOSE_PATH = re.compile(r'^/api/v1/assessment/process/attempts/(\d+)/writer-lease/release/$')
ENTRY_MATERIAL_FIELDS = ('reason','navigation_id','client_occurred_at','client_monotonic_ms','client_time_origin')


def js_json(value):
    """JSON.stringify after recursive JS Object.keys().sort(), including Number spelling."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite browser number')
        if number == 0:
            return '0'
        shortest = repr(number).lower()
        if 1e-6 <= abs(number) < 1e21:
            return format(Decimal(shortest), 'f').rstrip('0').rstrip('.') if '.' in format(Decimal(shortest), 'f') else format(Decimal(shortest), 'f')
        mantissa, exponent = shortest.split('e')
        mantissa = mantissa.rstrip('0').rstrip('.') if '.' in mantissa else mantissa
        power = int(exponent)
        return mantissa + 'e' + ('+' if power >= 0 else '-') + str(abs(power))
    if isinstance(value, str):
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return re.sub(r'[\ud800-\udfff]', lambda m: '\\u%04x' % ord(m.group()), encoded)
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(js_json(item) for item in value) + ']'
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('Browser object keys must be strings')
        # JSON.stringify enumerates array-index property names before ordinary keys.
        def order(key):
            index = key.isascii() and key.isdigit() and str(int(key)) == key and int(key) < 4294967295
            return (0, int(key)) if index else (1, key.encode('utf-16-be', errors='surrogatepass'))
        return '{' + ','.join(js_json(key) + ':' + js_json(value[key]) for key in sorted(value, key=order)) + '}'
    raise ValueError('Unsupported browser JSON value')


def js_digest(value):
    return hashlib.sha256(js_json(value).encode('utf-8')).hexdigest()


def require_isolated_database():
    if (os.environ.get('POSTGRES_DB') != 'kt_e2e' or connection.vendor != 'postgresql'
            or connection.settings_dict['NAME'] != 'kt_e2e'):
        raise CommandError('Only explicit direct isolated PostgreSQL kt_e2e is permitted')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        if cursor.fetchone() != ('kt_e2e',):
            raise CommandError('Actual database must be isolated kt_e2e')


def _uuid(value):
    if value is None:
        return None
    from uuid import UUID
    return str(UUID(str(value)))


def _event_identity(material, client):
    """Capture deduplicates by client and sequence; ordinary wire events omit UUID."""
    client = _uuid(client)
    sequence = material['client_sequence_no']
    if (client is None or type(sequence) is not int or sequence < 1
            or material.get('client_instance_id') is not None and _uuid(material['client_instance_id']) != client):
        raise ValueError('Invalid capture event identity')
    if material.get('event_id') is not None:
        _uuid(material['event_id'])
    return client, sequence


def _instant(value):
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Naive browser timestamp')
    return result


def _clock_matches(actual, expected):
    return (_instant(actual.client_wall_time_utc) == _instant(expected['client_wall_time_utc'])
        and _instant(actual.client_time_origin) == _instant(expected['client_time_origin'])
        and actual.client_monotonic_ms == Decimal(str(expected['client_monotonic_ms'])).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
        and _uuid(actual.time_sync_id) == _uuid(expected['time_sync_id']))


def _event_matches(actual, expected, client, student_id, session_id, attempts):
    projected = project_payload(actual.payload, expected['payload_fields'], expected['event_type'])
    return (actual.student_id == student_id and actual.session_id == session_id == expected['session_id']
        and actual.attempt_id == expected['attempt_id']
        and (actual.attempt_id is None or actual.attempt_id in attempts and attempts[actual.attempt_id].session_id == session_id)
        and _uuid(actual.client_instance_id) == _uuid(client)
        and (expected.get('event_id') is None or _uuid(expected['event_id']) == _uuid(actual.pk))
        and actual.client_sequence_no == expected['client_sequence_no']
        and actual.event_type == expected['event_type'] and actual.event_version == expected['event_version']
        and _uuid(actual.correlation_id) == _uuid(expected['correlation_id']) and _clock_matches(actual, expected)
        and js_digest(actual.app_version) == expected['app_version_sha256']
        and js_digest(actual.quality_flags) == expected['quality_flags_sha256']
        and js_digest(projected) == expected['payload_sha256'])


def _checkpoint_matches(actual, expected, student_id, session_id):
    return (set(expected['material_fields']) == CHECKPOINT_FIELDS and actual.student_id == student_id
        and actual.session_id == session_id == expected['session_id']
        and _uuid(actual.client_instance_id) == _uuid(expected['client_instance_id'])
        and _clock_matches(actual, expected)
        and all(getattr(actual, field) == expected[field] for field in ('trigger', 'highest_emitted_sequence_no',
            'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no', 'pending_event_count', 'failed_event_count'))
        and js_digest(actual.app_version) == expected['app_version_sha256']
        and js_digest(actual.quality_flags) == expected['quality_flags_sha256']
        and js_digest(actual.missing_local_sequence_ranges) == expected['missing_local_sequence_ranges_sha256'])


def _entry_matches(event, material, request, session, attempts, visits):
    match = ENTRY_PATH.fullmatch(request['path'])
    assignment_id = material.get('assignment_id') if request.get('transition') else int(match[2]) if match else None
    visit = visits.get(event.payload['visit_id'])
    if (assignment_id is None or event.session_id != session.pk or event.student_id != session.student_id
            or event.assignment_id != assignment_id or event.attempt_id not in attempts
            or attempts[event.attempt_id].session_id != session.pk or event.event_type != 'item_entered'
            or event.event_version != 3 or event.client_instance_id is not None or visit is None
            or visit.session_id != session.pk or visit.attempt_id != event.attempt_id
            or _uuid(event.correlation_id) != _uuid(material['navigation_id'])
            or material['reason'] not in {'navigation_click', 'resume'}
            or event.payload['entry_reason'] != material['reason'] or visit.entry_reason != material['reason']
            or _instant(event.client_wall_time_utc) != _instant(material['client_occurred_at'])
            or _instant(visit.client_occurred_at) != _instant(material['client_occurred_at'])
            or event.client_monotonic_ms != Decimal(str(material['client_monotonic_ms'])).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
            or _instant(event.client_time_origin) != _instant(material['client_time_origin'])):
        return False
    receipt = request.get('entry_receipt')
    if receipt is None:
        return True
    return (receipt['session_id'] == session.pk and receipt['assignment_id'] == event.assignment_id
        and receipt['attempt_id'] == event.attempt_id and receipt['visit_id'] == visit.pk
        and receipt['visit_index'] == visit.visit_index and _uuid(receipt['entry_event_id']) == _uuid(event.pk)
        and _uuid(receipt['navigation_id']) == _uuid(event.correlation_id)
        and _instant(receipt['entered_at']) == _instant(event.server_received_at))


def _safe_match(callback):
    try:
        return bool(callback())
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        return False


def _entry_projection_complete(material):
    return _safe_match(lambda: _instant(material['client_occurred_at']) is not None
        and _instant(material['client_time_origin']) is not None
        and Decimal(str(material['client_monotonic_ms'])).is_finite()
        and Decimal(str(material['client_monotonic_ms'])) >= 0)


def _candidate(fixture):
    source = fixture.get('source_config')
    source = source if isinstance(source, dict) else {}
    return {key: value if isinstance(value := source.get(key), str) and HASH.fullmatch(value) else None
            for key in CANDIDATE_FIELDS}


def _fixture_matches(fixture, task):
    return _safe_match(lambda: fixture['database'] == 'kt_e2e' and bool(fixture['run_id'])
        and (type(fixture['student_count']) is int and fixture['student_count'] >= 2
            and len(fixture['usernames']) == len(set(fixture['usernames'])) == fixture['student_count']
            and len(fixture['browser_usernames']) == len(set(fixture['browser_usernames'])) == 2
            and set(fixture['browser_usernames']) <= set(fixture['usernames']) and fixture['item_count'] == 20
            if 'browser_usernames' in fixture else fixture['student_count'] == 3
            and len(fixture['usernames']) == len(set(fixture['usernames'])) == 3)
        and all(isinstance(name, str) and name for name in fixture['usernames'])
        and type(fixture['task_id']) is int and type(fixture['course_id']) is int and type(fixture['class_id']) is int
        and task.pk == fixture['task_id'] and task.experiment_id == fixture['experiment_id']
        and task.experiment.course_id == fixture['course_id'] and task.class_group_id == fixture['class_id']
        and all(value is not None for value in _candidate(fixture).values()))


def natural_behavior_matches(probe, attempts, events):
    """A natural completion requires twenty unique answers and only planned optional actions."""
    def matches():
        plan = probe['natural_plan']
        if (len(attempts) != 20 or len({row.pk for row in attempts}) != 20
                or len({row.final_response_event_id for row in attempts}) != 20
                or any(row.status != 'submitted' or row.final_response_event_id is None for row in attempts)
                or probe['submitted_count'] != 20):
            return False
        for action, key in (('answer_modify', 'modify_items'), ('navigation_next_ready', 'excursion_items'),
                ('navigation_return_ready', 'excursion_items'), ('optional_tool', 'optional_tool_items')):
            actual = [row['sequence'] - 1 for row in probe['actions'] if row['action'] == action and row['completed']]
            if sorted(actual) != sorted(plan[key]):
                return False
        waits = probe['think_waits']
        if (len(waits) != 20 or {row['item'] for row in waits} != set(range(20))
                or any(row['planned_seconds'] != plan['item_dwell_seconds'][row['item']]
                    or row['completed_at'] - row['started_at'] + .01 < row['planned_seconds'] for row in waits)):
            return False
        transitions = [row for row in events if row.event_type == 'response_transition']
        return (len(transitions) >= 20 + len(plan['modify_items'])
            and {row.attempt_id for row in transitions} == {row.pk for row in attempts})
    return _safe_match(matches)


def browser_confidence_checks(plan, report, sessions, attempts, confidence, events):
    """Reconcile the two browser students with frozen choices and actual durable evidence."""
    checks = {key: True for key in ('browser_confidence_evidence_binding',
        'browser_confidence_exact_frozen_decisions_and_final_heads',
        'browser_confidence_exact_unique_prompt_and_terminal_events',
        'browser_confidence_exact_client_observations', 'browser_confidence_acknowledged_receipts_match',
        'browser_confidence_only_zero_tool_events_and_requests')}
    try:
        plans = {row['username']: row for row in plan['students'] if row['role'] == 'browser'}
        probes = {row['protocol_witness']['session_ids'][0]: row for row in report['probes']}
        assigned = {row.attempt_id: row for row in confidence}
        checks['browser_confidence_evidence_binding'] = (
            len(sessions) == len(probes) == len(plans) == 2
            and set(probes) == {row.pk for row in sessions}
            and set(plans) == {row.student.username for row in sessions}
            and all(isinstance(row.get('waterfall'), list) and row['waterfall']
                and all(isinstance(request.get('endpoint'), str) for request in row['waterfall']) for row in probes.values()))
        checks['browser_confidence_exact_frozen_decisions_and_final_heads'] = (
            len(attempts) == len(confidence) == len(assigned) == 40
            and set(assigned) == {row.pk for row in attempts})
        checks['browser_confidence_only_zero_tool_events_and_requests'] = (
            not any(row.event_type in ('research_tool', 'research_tool_requested', 'research_tool_observed') for row in events)
            and not any(re.search(r'/(?:tools|resolve)/$', request.get(field, ''))
                for probe in probes.values() for field, requests in (
                    ('endpoint', probe.get('waterfall', [])), ('path', probe['protocol_witness']['requests'])) for request in requests))
        for session in sessions:
            student, probe = plans[session.student.username], probes[session.pk]
            rows = sorted((row for row in attempts if row.session_id == session.pk), key=lambda row: row.assignment.assigned_sequence_index)
            checks['browser_confidence_exact_frozen_decisions_and_final_heads'] &= (
                len(rows) == 20 and {row.assignment.assigned_sequence_index for row in rows} == set(range(1, 21)))
            for index, attempt in enumerate(rows):
                decision = confidence_decision(student, index, plan['confidence_scale'], str(attempt.final_response_event_id))
                status, probability = decision['status'], decision.get('probability')
                row = assigned.get(attempt.pk)
                checks['browser_confidence_exact_frozen_decisions_and_final_heads'] &= (
                    row is not None and row.condition == 'prompt' and row.response_status == status
                    and row.probability == probability and row.prompt_shown_at is not None
                    and ((row.committed_at is not None) == (status == 'committed'))
                    and attempt.final_response_event_id is not None
                    and str(attempt.active_response_event_id) == str(attempt.final_response_event_id))
                actual = [event for event in events if event.attempt_id == attempt.pk]
                prompts = [event for event in actual if event.event_type == 'confidence_prompt_shown']
                terminals = [event for event in actual if event.event_type in ('confidence_committed', 'confidence_skipped')]
                checks['browser_confidence_exact_unique_prompt_and_terminal_events'] &= (
                    row is not None and len(prompts) == len(terminals) == 1
                    and prompts[0].client_instance_id is None and terminals[0].client_instance_id is None
                    and prompts[0].payload.get('confidence_assignment_id') == str(row.pk)
                    and prompts[0].payload.get('scale_version') == plan['confidence_scale_version']
                    and terminals[0].payload.get('confidence_assignment_id') == str(row.pk)
                    and terminals[0].event_type == ('confidence_committed' if status == 'committed' else 'confidence_skipped')
                    and (terminals[0].payload.get('probability') == probability if status == 'committed'
                        else terminals[0].payload.get('reason_code') == 'student_skipped'))
                observed = [event for event in actual if event.event_type == 'confidence_observed']
                checks['browser_confidence_exact_client_observations'] &= (
                    row is not None and Counter(event.payload.get('stage') for event in observed)
                        == Counter(['presented'] + (['skipped'] if status == 'skipped' else []))
                    and all(event.client_instance_id is not None and event.payload.get('confidence_assignment_id') == str(row.pk)
                        and event.payload.get('answer_event_id') == str(attempt.final_response_event_id)
                        and event.payload.get('probability') is None for event in observed))
                requests = [request for request in probe['protocol_witness']['requests'] if request.get('submission')
                    and request['path'] == f'/api/v1/student/attempts/{attempt.pk}/submit/']
                confirmed = [request for request in requests if type(request.get('status')) is int
                    and 200 <= request['status'] < 300 and request.get('receipt', {}).get('committed') is True]
                checks['browser_confidence_acknowledged_receipts_match'] &= bool(confirmed) and all(
                    request['submission']['token'] == str(attempt.submission_token)
                    and request['submission']['target_event_id'] == str(attempt.final_response_event_id)
                    and request['submission']['material_sha256'] == attempt.submission_fingerprint for request in requests) and all(
                    request['receipt']['final_response_event_id'] == str(attempt.final_response_event_id)
                    and request['receipt']['persisted_material_sha256'] == js_digest({
                        key: attempt.submission_receipt.get(key) for key in SUBMISSION_RECEIPT_FIELDS}) for request in confirmed)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        checks['browser_confidence_evidence_binding'] = False
    return checks


def _missing_ranges(highest, known):
    result, previous = [], 0
    for number in sorted({value for value in known if 0 < value <= highest}):
        if number > previous + 1:
            result.append([previous + 1, number - 1])
        previous = number
    if previous < highest:
        result.append([previous + 1, highest])
    return result


def _local_storage_observations(probes, sessions, attempts, assignments, observations, acknowledgements):
    """Read-only snapshot summaries extend the same immutable material comparisons."""
    checks = {'all_storage_reads_complete_and_scoped': len(probes) == len(sessions) and bool(probes),
        'local_record_counts_and_delivery_states_match': True, 'stream_boundaries_reconciled': True,
        'intent_snapshot_boundaries_reconciled': True, 'all_local_intent_material_available': True}
    ids = {kind: set() for kind in observations}
    unknown_intents = 0
    local_record_count = 0
    for probe in probes:
        try:
            evidence = probe['protocol_witness']
            session = sessions[evidence['session_ids'][0]]
            local = evidence['local_storage']
            if (local['schema_version'] != 1 or local['read_complete'] is not True or local['scope_complete'] is not True
                    or local['status'] != 'observed' or local.get('reason') is not None
                    or local['database'] != {'name': 'kt-process-events-v1', 'version': 2}
                    or local['session_ids'] != [session.pk]):
                raise ValueError('Storage read boundary unknown')
            clients = {_uuid(value) for value in local['client_instance_ids']}
            if not clients or None in clients or len(clients) != len(local['client_instance_ids']):
                raise ValueError('Unknown local client scope')
            cutoff = _instant(local['snapshot_cutoff_utc'])
            if cutoff is None:
                raise ValueError('Missing storage snapshot cutoff')
            retained = defaultdict(set)
            delivery = defaultdict(lambda: {'pending': 0, 'failed': 0})
            primary_ids = {'events': set(), 'checkpoints': set()}
            intent_boundaries = defaultdict(list)

            def append_material(kind, item, fallback_client=None, primary=False):
                client = _uuid(item.get('client_instance_id', fallback_client))
                key = _event_identity(item, client) if kind == 'events' else _uuid(item['checkpoint_id'])
                if (key is None or client not in clients or item['session_id'] != session.pk
                        or not HASH.fullmatch(item['material_sha256'])):
                    raise ValueError('Local material ownership or hash mismatch')
                if kind == 'events' and (item['attempt_id'] is not None
                        and (item['attempt_id'] not in attempts or attempts[item['attempt_id']].session_id != session.pk)):
                    raise ValueError('Local attempt outside fixture')
                if primary:
                    if key in primary_ids[kind]:
                        raise ValueError('Duplicate local record')
                    primary_ids[kind].add(key)
                    allowed = {'pending', 'failed'} if kind == 'events' else {'pending'}
                    if item['delivery_status'] not in allowed:
                        raise ValueError('Unknown local delivery status')
                    if kind == 'events':
                        sequence = item['client_sequence_no']
                        if type(sequence) is not int or sequence < 1:
                            raise ValueError('Invalid local sequence')
                        retained[client].add(sequence)
                        delivery[client][item['delivery_status']] += 1
                observations[kind][key].append((session, {'client_instance_id': client}, item))
                ids[kind].add(key)

            for kind in ('events', 'checkpoints'):
                for item in local[kind]:
                    append_material(kind, item, primary=True)
            for intent in local['intents']:
                kind = intent['kind']
                token = _uuid(intent['token'])
                if (kind not in {'entry', 'navigation', 'submission','close'} or token is None
                        or intent['session_id'] != session.pk or intent['delivery_status'] != 'unconfirmed'
                        or not HASH.fullmatch(intent['document_sha256'])):
                    raise ValueError('Local intent scope unknown')
                if token in ids[kind]:
                    raise ValueError('Duplicate local intent')
                ids[kind].add(token)
                snapshot = intent.get('snapshot')
                if snapshot:
                    snapshot_client = _uuid(snapshot['client_instance_id'])
                    snapshot_boundary = snapshot['event_boundary']
                    if (snapshot_client not in clients or type(snapshot_boundary) is not int or snapshot_boundary < 0):
                        raise ValueError('Invalid retained intent boundary')
                    intent_boundaries[snapshot_client].append(snapshot_boundary)
                    for nested in ('events', 'checkpoints'):
                        for item in snapshot[nested]:
                            sequence = item['client_sequence_no' if nested == 'events' else 'highest_emitted_sequence_no']
                            if (type(sequence) is not int or not 0 <= sequence <= snapshot_boundary
                                    or _uuid(item.get('client_instance_id', snapshot_client)) != snapshot_client):
                                raise ValueError('Retained intent material outside boundary')
                            append_material(nested, item, snapshot_client)
                material = intent.get('material')
                fingerprint = intent.get('material_sha256')
                if material is None or fingerprint is None:
                    unknown_intents += 1
                    checks['all_local_intent_material_available'] = False
                    continue
                if kind == 'entry' and not _entry_projection_complete(material):
                    unknown_intents += 1
                    checks['all_local_intent_material_available'] = False
                    continue
                if (not HASH.fullmatch(fingerprint) or material['material_sha256'] != fingerprint
                        or _uuid(material['navigation_id' if kind == 'entry' else 'token']) != token):
                    raise ValueError('Local intent material mismatch')
                if kind == 'entry':
                    assignment = intent['assignment_id']
                    if assignments.get(assignment) != session.pk:
                        raise ValueError('Local entry outside fixture')
                    path = f'/api/v1/student/sessions/{session.pk}/questions/{assignment}/'
                else:
                    attempt = intent['attempt_id']
                    if attempt not in attempts or attempts[attempt].session_id != session.pk:
                        raise ValueError('Local operation outside fixture')
                    path = (f'/api/v1/assessment/process/attempts/{attempt}/writer-lease/release/' if kind == 'close'
                        else f'/api/v1/student/attempts/{attempt}/' + ('draft/' if kind == 'navigation' else 'submit/'))
                observations[kind][token].append((session, {'path': path,'client_instance_id':snapshot['client_instance_id'] if snapshot else None}, material))
                if 'next_entry' in intent:
                    entry=intent['next_entry']; key=_uuid(entry['navigation_id'])
                    original={field:entry[field] for field in ENTRY_MATERIAL_FIELDS}
                    if (not _entry_projection_complete(entry) or key is None
                            or assignments.get(entry['assignment_id']) != session.pk
                            or entry['material_sha256'] != js_digest(original)
                            or intent['next_entry_sha256'] != js_digest({'assignment_id':entry['assignment_id'],**original})):
                        raise ValueError('Retained target entry material mismatch')
                    observations['entry'][key].append((session, {'path':f'/api/v1/student/sessions/{session.pk}/questions/{entry["assignment_id"]}/'},entry))
                    ids['entry'].add(key)
            boundary = local['boundary']
            pending = sum(item['delivery_status'] == 'pending' for item in local['events'])
            failed = sum(item['delivery_status'] == 'failed' for item in local['events'])
            checks['local_record_counts_and_delivery_states_match'] &= (
                boundary == {'event_count': len(local['events']), 'checkpoint_count': len(local['checkpoints']),
                    'intent_count': len(local['intents']), 'pending_event_count': pending,
                    'failed_event_count': failed, 'unscoped_client_count': 0})
            local_record_count += len(local['events']) + len(local['checkpoints']) + len(local['intents'])
            streams = local['streams']
            checks['stream_boundaries_reconciled'] &= (len(streams) == len(clients)
                and {_uuid(row['client_instance_id']) for row in streams} == clients)
            for stream in streams:
                client = _uuid(stream['client_instance_id'])
                highest = stream['highest_emitted_sequence_no']
                acked = stream['acknowledged_sequence_nos']
                if (type(highest) is not int or highest < 0 or not isinstance(acked, list)
                        or any(type(number) is not int or not 0 < number <= highest for number in acked)
                        or len(set(acked)) != len(acked)):
                    raise ValueError('Invalid local highwater')
                represented = {}
                network_acked = set()
                sequence_unique = True
                for key, rows in observations['events'].items():
                    for owner, request, item in rows:
                        if owner.pk != session.pk or _uuid(request.get('client_instance_id')) != client:
                            continue
                        sequence = item['client_sequence_no']
                        if cutoff is None or not request.get('sent_at') or _instant(request['sent_at']) <= cutoff:
                            if sequence in represented and represented[sequence] != key:
                                sequence_unique = False
                            represented[sequence] = key
                        if key in acknowledgements:
                            network_acked.add(sequence)
                contiguous = 0
                while contiguous + 1 in set(acked):
                    contiguous += 1
                checks['stream_boundaries_reconciled'] &= (
                    sequence_unique and len(represented) == highest
                    and (not represented or min(represented) == 1 and max(represented) == highest)
                    and set(acked) <= network_acked
                    and stream['highest_acked_sequence_no'] == max(acked, default=0)
                    and stream['highest_contiguous_acked_sequence_no'] == contiguous
                    and stream['pending_event_count'] == delivery[client]['pending']
                    and stream['failed_event_count'] == delivery[client]['failed']
                    and stream['missing_local_sequence_ranges'] == _missing_ranges(highest, set(acked) | retained[client]))
                checks['intent_snapshot_boundaries_reconciled'] &= all(
                    boundary <= highest for boundary in intent_boundaries[client])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ArithmeticError):
            checks['all_storage_reads_complete_and_scoped'] = False
            checks['local_record_counts_and_delivery_states_match'] = False
            checks['stream_boundaries_reconciled'] = False
    return checks, ids, unknown_intents, local_record_count


def verify_witness(fixture, report, *, task, sessions, events, checkpoints, attempts, visits, navigations, submissions,
                   assignments=None, plan=None, confidence=None, mutations=None):
    """No writes or repair. Missing unacknowledged facts remain unknown."""
    checks = {}
    session_map = {row.pk: row for row in sessions}
    attempt_map = {row.pk: row for row in attempts}
    assignment_session = ({row.pk: row.session_id for row in assignments} if assignments is not None
                          else {row.assignment_id: row.session_id for row in attempts})
    visits_map = {row.pk: row for row in visits}
    candidate = _candidate(fixture)
    probes = report.get('probes', [])
    browser_names = fixture.get('browser_usernames', fixture.get('usernames', []))
    browser_count = len(browser_names)
    checks['fixture_task_course_class_binding'] = _fixture_matches(fixture, task)
    checks['three_independent_fixture_sessions_students'] = _safe_match(lambda: len(sessions) == len(session_map) == browser_count
        and {row.student.username for row in sessions} == set(browser_names)
        and len({row.student_id for row in sessions}) == browser_count
        and all(row.class_task_id == task.pk for row in sessions)
        and all(row.session_id in session_map for row in attempts))
    checks['real_nonempty_candidate_bound_probes'] = _safe_match(lambda: len(probes) == browser_count
        and {row['protocol_witness']['probe'] for row in probes} == set(range(1, browser_count + 1))
        and all(row['protocol_witness']['schema_version'] == 1
            and row['protocol_witness']['evidence_kind'] == 'real_browser_network_witness'
            and row['protocol_witness']['candidate'] == candidate
            and row['protocol_witness']['task_id'] == task.pk
            and len(row['protocol_witness']['session_ids']) == 1 and row['protocol_witness']['requests']
            and all(any(request.get(kind) for request in row['protocol_witness']['requests'])
                    for kind in ('events', 'checkpoints', 'entry'))
            for row in probes)
        and {row['protocol_witness']['session_ids'][0] for row in probes} == set(session_map))
    observations = {name: defaultdict(list) for name in ('events', 'checkpoints', 'entry', 'navigation', 'submission', 'close')}
    acknowledgements = defaultdict(list)
    acknowledged_checkpoints = set()
    known_entry = set()
    known_navigation = set()
    known_submission = set()
    known_close = set()
    request_count = 0
    checks['request_and_ack_shapes_and_session_ownership'] = True
    settled = True
    for probe in probes:
        try:
            evidence = probe['protocol_witness']
            session_id = evidence['session_ids'][0]
            session = session_map[session_id]
            settlement = evidence.get('settlement') or {}
            settled &= (settlement.get('complete') is True and settlement.get('pending_response_count') == 0
                        and settlement.get('unfinished_request_count') == 0)
            for index, request in enumerate(evidence['requests']):
                request_count += 1
                if (request['request_index'] != index or not PROTOCOL_PATH.fullmatch(request['path'])
                        or not HASH.fullmatch(request['material_sha256'])):
                    raise ValueError('Invalid request observation')
                path_attempt = ATTEMPT_PATH.fullmatch(request['path'])
                if path_attempt and (int(path_attempt[1]) not in attempt_map or attempt_map[int(path_attempt[1])].session_id != session_id):
                    raise ValueError('Attempt outside probe')
                path_entry = ENTRY_PATH.fullmatch(request['path'])
                if path_entry and (int(path_entry[1]) != session_id or assignment_session.get(int(path_entry[2])) != session_id):
                    raise ValueError('Assignment outside probe')
                if request.get('transition'):
                    transition = request['transition']
                    source = path_attempt or CLOSE_PATH.fullmatch(request['path'])
                    if (not source or transition['source_attempt_id'] != int(source[1])
                            or int(source[1]) not in attempt_map or attempt_map[int(source[1])].session_id != session_id
                            or not (request.get('navigation') or request.get('close'))
                            or assignment_session.get(request['entry']['assignment_id']) != session_id
                            or transition['outcome'] not in {'committed','retry_required','unknown'}
                            or not HASH.fullmatch(transition['request_material_sha256'])):
                        raise ValueError('Transition outside bound source and target')
                    if transition['outcome'] == 'committed' and not request.get('entry_receipt'):
                        raise ValueError('Target committed without entry receipt')
                    if transition['outcome'] != 'committed' and request.get('entry_receipt'):
                        raise ValueError('Unconfirmed target cannot advertise a receipt')
                emitted_events = {_event_identity(item, request.get('client_instance_id')) for item in request['events']}
                emitted_checkpoints = {_uuid(item['checkpoint_id']) for item in request['checkpoints']}
                for kind, id_field in (('events', 'event_id'), ('checkpoints', 'checkpoint_id')):
                    for item in request[kind]:
                        if item['session_id'] != session_id or not HASH.fullmatch(item['material_sha256']):
                            raise ValueError('Material outside probe')
                        key = (_event_identity(item, request.get('client_instance_id')) if kind == 'events'
                               else _uuid(item[id_field]))
                        if key is None:
                            raise ValueError('Missing material identity')
                        observations[kind][key].append((session, request, item))
                for ack in request.get('acknowledgements', []):
                    key = _event_identity(ack, request.get('client_instance_id'))
                    if (key not in emitted_events or not 200 <= request.get('status', 0) < 300
                            or _uuid(ack['event_id']) is None
                            or type(ack['server_ingest_sequence_no']) is not int or ack['server_ingest_sequence_no'] < 1):
                        raise ValueError('ACK outside actual successful emission')
                    acknowledgements[key].append(ack)
                for key in request.get('checkpoint_ids', []):
                    if _uuid(key) not in emitted_checkpoints or not 200 <= request.get('status', 0) < 300:
                        raise ValueError('Checkpoint ACK outside emission')
                    acknowledged_checkpoints.add(_uuid(key))
                for kind, id_field, known in (('entry', 'navigation_id', known_entry), ('navigation', 'token', known_navigation),
                                             ('submission', 'token', known_submission), ('close','token',known_close)):
                    if kind in request:
                        material = request[kind]
                        key = _uuid(material[id_field])
                        if key is None or not HASH.fullmatch(material['material_sha256']):
                            raise ValueError('Missing operation identity or hash')
                        if kind == 'entry' and not _entry_projection_complete(material):
                            raise ValueError('Original entry clock projection unavailable')
                        observations[kind][key].append((session, request, material))
                        receipt = request.get('entry_receipt' if kind == 'entry' else 'receipt')
                        if receipt and 200 <= request.get('status', 0) < 300:
                            if kind != 'entry' and receipt.get('committed') is not True:
                                raise ValueError('Noncommitted receipt')
                            known.add(key)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            checks['request_and_ack_shapes_and_session_ownership'] = False
            settled = False
    checks['nonempty_emitted_events_checkpoints_and_entries'] = all(observations[kind] for kind in ('events', 'checkpoints', 'entry'))
    network_ids = {kind: set(rows) for kind, rows in observations.items()}
    local_checks, local_ids, unknown_intents, local_record_count = _local_storage_observations(
        probes, session_map, attempt_map, assignment_session, observations, acknowledgements)
    checks['same_identity_replays_preserve_request_material'] = all(
        len({row[2]['material_sha256'] for row in rows}) == 1
        for kind in observations.values() for rows in kind.values())
    client_events = [row for row in events if row.client_instance_id is not None]
    event_map = {(_uuid(row.client_instance_id), row.client_sequence_no): row for row in client_events}
    checkpoint_map = {_uuid(row.pk): row for row in checkpoints}
    checks['zero_extra_or_duplicate_client_events_checkpoints'] = (
        len(event_map) == len(client_events) and set(event_map) <= set(observations['events'])
        and len(checkpoint_map) == len(checkpoints) and set(checkpoint_map) <= set(observations['checkpoints']))
    checks['unique_client_sequences_and_session_ingest_ordinals'] = (
        len(event_map) == len(client_events) and len({_uuid(row.pk) for row in client_events}) == len(client_events)
        and all(type(row.client_sequence_no) is int and row.client_sequence_no > 0 for row in client_events)
        and len({(row.session_id, row.server_ingest_sequence_no) for row in events}) == len(events))
    checks['acknowledged_events_and_present_unknown_material_match'] = set(acknowledgements) <= set(event_map)
    for key, rows in observations['events'].items():
        if key not in event_map:
            continue
        event = event_map[key]
        checks['acknowledged_events_and_present_unknown_material_match'] &= all(_safe_match(lambda:
            _event_matches(event, material, request['client_instance_id'], session.student_id, session.pk, attempt_map))
            for session, request, material in rows)
        checks['acknowledged_events_and_present_unknown_material_match'] &= all(
            _uuid(ack['event_id']) == _uuid(event.pk) and ack['client_sequence_no'] == event.client_sequence_no
            and ack['server_ingest_sequence_no'] == event.server_ingest_sequence_no
            for ack in acknowledgements.get(key, []))
    checks['acknowledged_checkpoints_and_present_unknown_all_fields_match'] = acknowledged_checkpoints <= set(checkpoint_map)
    for key, rows in observations['checkpoints'].items():
        if key in checkpoint_map:
            checks['acknowledged_checkpoints_and_present_unknown_all_fields_match'] &= all(_safe_match(lambda:
                _checkpoint_matches(checkpoint_map[key], material, session.student_id, session.pk)) for session, _, material in rows)
    entries = defaultdict(list)
    for event in events:
        if event.event_type == 'item_entered' and event.correlation_id is not None:
            entries[_uuid(event.correlation_id)].append(event)
    checks['entry_receipts_and_present_unknown_entries_match'] = known_entry <= set(entries) and set(entries) <= set(observations['entry'])
    for key, rows in observations['entry'].items():
        if key in entries:
            checks['entry_receipts_and_present_unknown_entries_match'] &= len(entries[key]) == 1 and all(_safe_match(lambda:
                _entry_matches(entries[key][0], material, request, session, attempt_map, visits_map)) for session, request, material in rows)
    checks['all_durable_visits_have_observed_entries'] = _safe_match(lambda: {row.pk for row in visits} == {
        event.payload['visit_id'] for rows in entries.values() for event in rows})
    navigation_map = {_uuid(row.token): row for row in navigations}
    checks['navigation_receipts_and_present_unknown_match'] = (len(navigation_map) == len(navigations)
        and known_navigation <= set(navigation_map) and set(navigation_map) <= set(observations['navigation']))
    all_events = {_uuid(row.pk): row for row in events}
    for key, rows in observations['navigation'].items():
        if key not in navigation_map:
            continue
        stored = navigation_map[key]
        def matches_navigation(session, request, material):
            receipt = stored.receipt
            left = all_events.get(_uuid(material['leave_event_id']))
            target = _uuid(material['target_event_id'])
            return (stored.attempt_id in attempt_map and attempt_map[stored.attempt_id].session_id == session.pk
                and int(ATTEMPT_PATH.fullmatch(request['path'])[1]) == stored.attempt_id
                and receipt['committed'] is True and receipt['writer_lease_released'] is True
                and _uuid(receipt['navigation_token']) == key
                and receipt['to_assignment_id'] == material['to_assignment_id']
                and assignment_session.get(material['to_assignment_id']) == session.pk
                and _uuid(receipt['active_response_event_id']) == target
                and (target is None or target in all_events and all_events[target].attempt_id == stored.attempt_id)
                and left is not None and left.attempt_id == stored.attempt_id and left.event_type == 'item_left'
                and _uuid(left.correlation_id) == key
                and left.payload == {'leave_reason': 'navigation', 'to_assignment_id': material['to_assignment_id']}
                and (not request.get('transition') or stored.material_fingerprint == material['material_sha256'])
                and (not request.get('receipt') or js_digest({field: receipt.get(field) for field in NAVIGATION_RECEIPT_FIELDS})
                     == request['receipt']['persisted_material_sha256']))
        checks['navigation_receipts_and_present_unknown_match'] &= all(_safe_match(lambda:
            matches_navigation(session, request, material)) for session, request, material in rows)
    close_map = {_uuid(row.token):row for row in mutations or [] if row.kind == 'close'}
    checks['close_receipts_and_present_unknown_match'] = known_close <= set(close_map) and set(close_map) <= set(observations['close'])
    for key, rows in observations['close'].items():
        if key not in close_map:
            continue
        stored = close_map[key]
        def matches_close(session, request, material):
            left = all_events.get(_uuid(material['leave_event_id']))
            return (stored.attempt_id in attempt_map and attempt_map[stored.attempt_id].session_id == session.pk
                and int(CLOSE_PATH.fullmatch(request['path'])[1]) == stored.attempt_id
                and stored.material_fingerprint == material['material_sha256']
                and stored.receipt['committed'] is True and stored.receipt['writer_lease_released'] is True
                and _uuid(stored.receipt['capture_operation_id']) == key
                and left is not None and left.event_type == 'item_left' and left.attempt_id == stored.attempt_id
                and left.session_id == session.pk and _uuid(left.client_instance_id) == _uuid(request['client_instance_id'])
                and (not request.get('receipt') or js_digest({field:stored.receipt.get(field) for field in CLOSE_RECEIPT_FIELDS})
                    == request['receipt']['persisted_material_sha256']))
        checks['close_receipts_and_present_unknown_match'] &= all(_safe_match(lambda:
            matches_close(session,request,material)) for session,request,material in rows)
    checks['transition_source_and_target_match'] = True
    for probe in probes:
        ids=probe.get('protocol_witness',{}).get('session_ids',[])
        session = session_map.get(ids[0] if ids else None)
        for request in probe.get('protocol_witness',{}).get('requests',[]):
            if not request.get('transition'):
                continue
            def matches_transition():
                material=request['entry']; transition=request['transition']
                original={field:material[field] for field in ENTRY_MATERIAL_FIELDS}
                source=attempt_map[transition['source_attempt_id']]
                if (transition['request_material_sha256'] != js_digest({'assignment_id':material['assignment_id'],**original})
                        or material['material_sha256'] != js_digest(original)
                        or source.assignment_id == material['assignment_id']):
                    return False
                if request.get('navigation') and request['navigation']['to_assignment_id'] != material['assignment_id']:
                    return False
                target=entries.get(_uuid(material['navigation_id']),[])
                if target and target[0].payload['from_assignment_id'] != source.assignment_id:
                    return False
                if transition['outcome'] != 'committed':
                    return True
                receipt=request['entry_receipt']; dto=transition['runtime_identity']; process=dto['process']
                if (dto['session_id'] != session.pk or dto['assignment_id'] != material['assignment_id']
                        or dto['attempt_id'] != receipt['attempt_id'] or process != receipt):
                    return False
                attempt=attempt_map[dto['attempt_id']]
                deadlines=[value for value in (getattr(attempt,'current_effective_deadline_at',None)
                    or getattr(attempt,'initial_effective_deadline_at',None),getattr(session,'deadline_at',None),
                    getattr(session,'task_closes_at',None)) if value is not None]
                deadline=min(deadlines) if deadlines else None
                if dto['entry_allowed'] is not True:
                    return True
                if deadline is None:
                    return dto['remaining_seconds'] is None and dto['presented_deadline_at'] is None
                presented=_instant(dto['presented_deadline_at']); entered=_instant(receipt['entered_at'])
                return (type(dto['remaining_seconds']) is int and dto['remaining_seconds'] >= 0
                    and entered <= deadline and presented is not None and abs((presented-deadline).total_seconds()) <= 1)
            checks['transition_source_and_target_match'] &= _safe_match(matches_transition)
    submission_map = {_uuid(row.submission_token): row for row in submissions}
    checks['submission_receipts_and_present_unknown_match'] = (len(submission_map) == len(submissions)
        and known_submission <= set(submission_map) and set(submission_map) <= set(observations['submission']))
    for key, rows in observations['submission'].items():
        if key not in submission_map:
            continue
        stored = submission_map[key]
        def matches_submission(session, request, material):
            attempt = attempt_map[stored.attempt_id]
            target = _uuid(material['target_event_id'])
            return (attempt.session_id == session.pk and int(ATTEMPT_PATH.fullmatch(request['path'])[1]) == attempt.pk
                and _uuid(attempt.submission_token) == key and attempt.status == 'submitted'
                and _uuid(attempt.final_response_event_id) == target and _uuid(attempt.active_response_event_id) == target
                and target in all_events and all_events[target].attempt_id == attempt.pk
                and attempt.submission_receipt['committed'] is True
                and _uuid(attempt.submission_receipt['final_response_event_id']) == target
                and (not request.get('receipt') or js_digest({field: attempt.submission_receipt.get(field) for field in SUBMISSION_RECEIPT_FIELDS})
                     == request['receipt']['persisted_material_sha256']))
        checks['submission_receipts_and_present_unknown_match'] &= all(_safe_match(lambda:
            matches_submission(session, request, material)) for session, request, material in rows)
    counts = {'requests': request_count, 'client_events': len(client_events), 'checkpoints': len(checkpoints),
        'entries': len(entries), 'navigation_receipts': len(navigations), 'submissions': len(submissions)}
    all_confirmed = True
    for kind, known, actual in (('events', set(acknowledgements), set(event_map)),
        ('checkpoints', acknowledged_checkpoints, set(checkpoint_map)), ('entry', known_entry, set(entries)),
        ('navigation', known_navigation, set(navigation_map)), ('submission', known_submission, set(submission_map)),
        ('close',known_close,set(close_map))):
        unknown = set(observations[kind]) - known
        counts.update({f'acknowledged_{kind}': len(known), f'unknown_{kind}': len(unknown),
            f'unknown_{kind}_persisted': len(unknown & actual), f'unknown_{kind}_missing': len(unknown - actual)})
        all_confirmed &= not (network_ids[kind] - known)
        local_unknown = local_ids[kind] - known
        counts.update({f'local_{kind}_acknowledged': len(local_ids[kind] & known),
            f'local_{kind}_unacknowledged': len(local_unknown),
            f'local_{kind}_unacknowledged_persisted': len(local_unknown & actual),
            f'local_{kind}_unacknowledged_missing': len(local_unknown - actual)})
    counts['local_intents_unknown_material'] = unknown_intents
    counts['local_record_count'] = local_record_count
    if plan is not None and plan.get('scenario') == 'confidence-only-v1':
        checks.update(browser_confidence_checks(plan, report, sessions, attempts, confidence or [], events))
        counts['browser_confidence_assignments'] = len(confidence or [])
    binding_keys = ('fixture_task_course_class_binding', 'three_independent_fixture_sessions_students',
        'real_nonempty_candidate_bound_probes', 'request_and_ack_shapes_and_session_ownership',
        'nonempty_emitted_events_checkpoints_and_entries')
    if 'browser_confidence_evidence_binding' in checks:
        binding_keys += ('browser_confidence_evidence_binding',)
    binding = all(checks[key] for key in binding_keys)
    integrity = all(checks.values())
    natural_passed = True
    if 'browser_usernames' in fixture:
        natural_passed = _safe_match(lambda: report['workload_version'] == 'natural-180-v1' and plan is not None
            and all(natural_behavior_matches(probe,
                [row for row in attempts if row.session_id == probe['protocol_witness']['session_ids'][0]],
                [row for row in events if row.session_id == probe['protocol_witness']['session_ids'][0]])
                and len([row for row in visits if row.session_id == probe['protocol_witness']['session_ids'][0]])
                    == 20 + 2 * len(probe['natural_plan']['excursion_items'])
                for probe in probes)
            and (plan['workload_version'] == 'natural-180-v1' and plan['task_id'] == fixture['task_id']
                and all(probe['natural_plan'] == next(row for row in plan['students']
                    if row['username'] == session_map[probe['protocol_witness']['session_ids'][0]].student.username
                    and row['role'] == 'browser') for probe in probes)))
    completed = (natural_passed and _safe_match(lambda: report['completed'] is True and len(probes) == browser_count
        and all(row['completed'] is True for row in probes)) and len(sessions) == browser_count
        and all(row.status == 'completed' and row.terminal_at is not None for row in sessions))
    local_complete = local_checks['all_storage_reads_complete_and_scoped']
    local_boundary = (local_complete and local_checks['local_record_counts_and_delivery_states_match']
        and local_checks['stream_boundaries_reconciled'] and local_checks['intent_snapshot_boundaries_reconciled'])
    known = {'events': set(acknowledgements), 'checkpoints': acknowledged_checkpoints,
             'entry': known_entry, 'navigation': known_navigation, 'submission': known_submission, 'close':known_close}
    local_confirmed = unknown_intents == 0 and all(local_ids[kind] <= known[kind] for kind in local_ids)
    network_passed = integrity and settled and all_confirmed and completed
    full_passed = network_passed and local_boundary and local_checks['all_local_intent_material_available'] and local_confirmed
    return {'database': 'kt_e2e', 'verification_mode': 'read_only_browser_network_and_storage_reconciliation', 'persistent_writes': False,
        'evidence_kind': 'real_browser_network_and_local_persistence', 'candidate': candidate, 'checks': checks, 'counts': counts,
        'evidence_binding_passed': binding, 'known_fact_integrity_passed': integrity,
        'raw_fact_integrity_failed': binding and not integrity,
        'transport_observations_settled': settled, 'all_emitted_material_acknowledged': all_confirmed,
        'workflow_completed': completed, 'passed': full_passed, 'network_witness_passed': network_passed,
        'natural_behavior_passed': natural_passed,
        'verification_scope': 'observed_network_and_read_only_indexeddb_material', 'local_storage_checks': local_checks,
        'local_storage_read_complete': local_complete, 'local_boundary_reconciled': local_boundary,
        'all_known_local_material_acknowledged': local_confirmed,
        'local_storage_boundary': {'unsent_indexeddb_events': 'covered' if local_boundary else 'unknown',
            'unsent_indexeddb_checkpoints': 'covered' if local_boundary else 'unknown',
            'retained_local_material_count': local_record_count if local_complete else None,
            'unsent_material_count': sum(len(local_ids[kind] - network_ids[kind]) for kind in local_ids) if local_complete else None},
        'full_browser_capture_chain_passed': full_passed,
        'status': ('passed' if full_passed else ('mismatch' if binding and not integrity else
                   ('incomplete' if binding else 'invalid_evidence'))),
        'scope': 'Observed network, complete scoped IndexedDB snapshot, actual ACKs and durable facts only; no scoring, Coverage, export or capacity claim.'}


class Command(BaseCommand):
    help = 'Read-only actual browser witness reconciliation. Explicit direct PostgreSQL kt_e2e only.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--browser-report', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--plan')

    def handle(self, *args, **options):
        require_isolated_database()
        try:
            fixture = json.loads(Path(options['manifest']).read_text(encoding='utf-8-sig'))
            report = json.loads(Path(options['browser_report']).read_text(encoding='utf-8-sig'))
            plan = json.loads(Path(options['plan']).read_text(encoding='utf-8-sig')) if options.get('plan') else None
        except (OSError, ValueError):
            raise CommandError('Private fixture and browser report must be readable JSON') from None
        if not isinstance(fixture, dict) or not isinstance(report, dict):
            raise CommandError('Private fixture and browser report must be JSON objects')
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                task = ClassExperimentTask.objects.select_related('experiment').filter(pk=fixture.get('task_id')).first()
                if not _fixture_matches(fixture, task):
                    raise CommandError('Dedicated fixture task/course/class/candidate and explicit browser account binding required')
                scoped = ExperimentSession.objects.filter(class_task_id=task.pk)
                if scoped.exclude(student__username__in=fixture['usernames']).exists():
                    raise CommandError('Task contains sessions outside the private fixture')
                sessions = list(scoped.filter(student__username__in=fixture.get('browser_usernames',fixture['usernames'])).select_related('student'))
                ids = [row.pk for row in sessions]
                result = verify_witness(fixture, report, task=task, sessions=sessions, plan=plan,
                    confidence=list(ConfidenceAssignment.objects.filter(attempt__session_id__in=ids))
                        if plan is not None and plan.get('scenario') == 'confidence-only-v1' else None,
                    events=list(AssessmentEvent.objects.filter(session_id__in=ids)),
                    checkpoints=list(ClientCaptureCheckpoint.objects.filter(session_id__in=ids)),
                    attempts=list(Attempt.objects.filter(session_id__in=ids)),
                    assignments=list(StudentQuestionAssignment.objects.filter(session_id__in=ids)),
                    visits=list(QuestionVisitEvent.objects.filter(session_id__in=ids)),
                    navigations=list(NavigationReceipt.objects.filter(attempt__session_id__in=ids)),
                    mutations=list(CaptureMutationReceipt.objects.filter(attempt__session_id__in=ids,kind='close')),
                    submissions=list(SubmissionEvent.objects.filter(attempt__session_id__in=ids)))
        except CommandError:
            raise
        except Exception:
            # SQL and private material never enter diagnostics, including unexpected read failures.
            result = {'database': 'kt_e2e', 'verification_mode': 'read_only_browser_network_and_storage_reconciliation',
                'persistent_writes': False, 'passed': False, 'status': 'read_failed',
                'known_fact_integrity_passed': False, 'raw_fact_integrity_failed': False,
                'network_witness_passed': False, 'verification_scope': 'observed_network_and_read_only_indexeddb_material',
                'local_storage_read_complete': False, 'local_boundary_reconciled': False,
                'all_known_local_material_acknowledged': False,
                'local_storage_boundary': {'unsent_indexeddb_events': 'unknown',
                    'unsent_indexeddb_checkpoints': 'unknown', 'unsent_material_count': None},
                'full_browser_capture_chain_passed': False, 'checks': {'database_reads_succeeded': False}}
        try:
            with Path(options['output']).open('x', encoding='utf-8') as output:
                json.dump(result, output, indent=2, ensure_ascii=False)
        except OSError:
            raise CommandError('Private output must be writable and must not already exist') from None
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if not result['passed']:
            raise CommandError('Browser witness verification incomplete or mismatched; inspect aggregate checks')
