"""Read-only persistence accounting for the capture protocol ledger."""
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.assessment.models import (AssessmentEvent, Attempt, ClientCaptureCheckpoint, ClientTimeSync,
    ConfidenceAssignment, ExperimentSession, NavigationReceipt, QuestionVisitEvent, SubmissionEvent)
from apps.assessment.response_snapshot_services import canonical_response_state
from scripts.load.capture_protocol import BROWSER_ALIGNED_WORKLOAD, CONTRACT, digest, project_payload


def event_matches(event, expected, student_id, *, require_ack=True):
    try:
        return (event.student_id == student_id and event.session_id == expected['session_id']
            and event.attempt_id == expected['attempt_id'] and event.event_type == expected['event_type']
            and event.event_version == expected['event_version']
            and str(event.client_instance_id) == expected['client_instance_id']
            and event.client_sequence_no == expected['client_sequence_no']
            and (event.server_ingest_sequence_no == expected['server_ingest_sequence_no']
                 or (not require_ack and expected['server_ingest_sequence_no'] is None
                     and event.server_ingest_sequence_no > 0))
            and (not require_ack or (expected['acknowledged'] is True and expected['ack_count'] > 0))
            and digest(project_payload(event.payload, expected['payload_fields'], event.event_type)) == expected['payload_sha256'])
    except (KeyError, TypeError, ValueError):
        return False


def _field_matches(row, key, value):
    field = 'pk' if key in ('checkpoint_id', 'time_sync_id') else key
    actual = getattr(row, field)
    if 'wall_time' in key or key == 'client_time_origin':
        return actual == datetime.fromisoformat(value.replace('Z', '+00:00'))
    if 'monotonic_ms' in key:
        return actual == Decimal(str(value))
    if key in ('client_instance_id', 'checkpoint_id', 'time_sync_id', 'correlation_id'):
        return (str(actual) if actual is not None else None) == value
    return actual == value


def checkpoint_matches(row, expected, student_id, *, require_ack=True):
    try:
        document = expected['document']
        fields = {'checkpoint_id', 'session_id', 'client_instance_id', 'highest_emitted_sequence_no',
            'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no', 'pending_event_count',
            'failed_event_count', 'missing_local_sequence_ranges', 'client_monotonic_ms', 'client_wall_time_utc',
            'client_time_origin', 'time_sync_id', 'trigger', 'quality_flags', 'app_version'}
        return (set(document) == fields and row.student_id == student_id
            and (not require_ack or expected['acknowledged'] is True)
            and expected['emission_count'] > 0 and digest(document) == expected['payload_sha256']
            and all(_field_matches(row, key, value) if key != 'time_sync_id' else
                    (str(row.time_sync_id) if row.time_sync_id is not None else None) == value
                    for key, value in document.items()))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def time_sync_matches(row, expected, session_id):
    try:
        return (row.session_id == session_id and expected['finalized'] is True
            and row.sync_quality == 'valid' and row.finalized_at is not None
            and row.estimated_rtt_ms is not None and row.estimated_clock_offset_ms is not None
            and all(_field_matches(row, key, value) for key, value in expected['create'].items())
            and all(_field_matches(row, key, value) for key, value in expected['finalize'].items()))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def browser_event_envelope_matches(row, expected):
    try:
        return expected is not None and all(_field_matches(row, key, expected[key]) if key != 'time_sync_id' else
            (str(row.time_sync_id) if row.time_sync_id is not None else None) == expected[key]
            for key in ('client_wall_time_utc', 'client_monotonic_ms', 'client_time_origin', 'time_sync_id', 'app_version', 'correlation_id'))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def entry_matches(event, visit, session_id):
    try:
        material, receipt = visit['entry_material'], visit['entry_receipt']
        return (event is not None and event.event_type == 'item_entered' and event.session_id == int(session_id)
            and event.attempt_id == receipt['attempt_id'] == visit['attempt_id']
            and event.assignment_id == receipt['assignment_id'] == visit['assignment_id']
            and str(event.pk) == receipt['entry_event_id'] == visit['entry_event_id']
            and str(event.correlation_id) == receipt['navigation_id'] == material['navigation_id']
            and event.payload['visit_id'] == receipt['visit_id'] == visit['visit_id']
            and event.payload['entry_reason'] == material['reason']
            and event.client_wall_time_utc == datetime.fromisoformat(material['client_occurred_at'].replace('Z', '+00:00'))
            and _field_matches(event, 'client_monotonic_ms', material['client_monotonic_ms'])
            and _field_matches(event, 'client_time_origin', material['client_time_origin']))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def known_fact_integrity(fixture, ledger, sessions, events, checkpoints, attempts=None):
    """ACK certainty and durable material agreement are separate from workflow completion."""
    session_map = {str(row.pk): row for row in sessions}
    event_rows = [row for row in events if row.client_instance_id is not None]
    actual_events = {str(row.pk): row for row in event_rows}
    all_event_rows = {str(row.pk): row for row in events}
    actual_checkpoints = {str(row.pk): row for row in checkpoints}
    expected_events = {key: (session_id, value) for session_id, row in ledger['sessions'].items()
                       for key, value in row['events'].items()}
    expected_checkpoints = {key: (session_id, value) for session_id, row in ledger['sessions'].items()
                            for key, value in row.get('checkpoints', {}).items()}
    acked_events = {key for key, (_, value) in expected_events.items() if value.get('acknowledged') is True}
    acked_checkpoints = {key for key, (_, value) in expected_checkpoints.items() if value.get('acknowledged') is True}
    unknown_events = set(expected_events) - acked_events
    unknown_checkpoints = set(expected_checkpoints) - acked_checkpoints
    session_binding = all(session_id in session_map and session_map[session_id].student.username in fixture['usernames']
        and (row.get('username_sha256') is None or row['username_sha256'] == digest(session_map[session_id].student.username))
        for session_id, row in ledger['sessions'].items())
    attempt_map = {row.pk: row for row in attempts} if attempts is not None else None
    matched_events = {}
    for key, (session_id, value) in expected_events.items():
        actual = actual_events.get(key)
        session = session_map.get(session_id)
        matched_events[key] = (actual is not None and session is not None
            and event_matches(actual, value, session.student_id, require_ack=key in acked_events)
            and browser_event_envelope_matches(actual, value)
            and (attempt_map is None or actual.attempt_id is None or
                 ((attempt := attempt_map.get(actual.attempt_id)) is not None and attempt.session_id == actual.session_id)))
    matched_checkpoints = {}
    for key, (session_id, value) in expected_checkpoints.items():
        actual = actual_checkpoints.get(key)
        session = session_map.get(session_id)
        matched_checkpoints[key] = (actual is not None and session is not None
            and checkpoint_matches(actual, value, session.student_id, require_ack=key in acked_checkpoints))
    checks = {'substantive_acknowledged_event_evidence': bool(acked_events),
        'all_confirmed_entries_match_original_material': all(
            entry_matches(all_event_rows.get(visit['entry_event_id']), visit, session_id)
            for session_id, row in ledger['sessions'].items() for visit in row.get('visits', [])),
        'known_sessions_bound_to_fixture_students': bool(ledger['sessions']) and session_binding,
        'all_acknowledged_events_persisted_and_material_matched': all(matched_events[key] for key in acked_events),
        'present_unacknowledged_events_material_matched': all(matched_events[key] for key in unknown_events & set(actual_events)),
        'all_acknowledged_checkpoints_persisted_and_original_fields_matched': all(matched_checkpoints[key] for key in acked_checkpoints),
        'present_unacknowledged_checkpoints_original_fields_matched': all(matched_checkpoints[key] for key in unknown_checkpoints & set(actual_checkpoints)),
        'zero_unexpected_or_duplicate_client_events': len(actual_events) == len(event_rows) and set(actual_events) <= set(expected_events),
        'zero_unexpected_or_duplicate_checkpoints': len(actual_checkpoints) == len(checkpoints) and set(actual_checkpoints) <= set(expected_checkpoints),
        'unique_known_client_sequences_and_ingest_ordinals': len({(row.client_instance_id, row.client_sequence_no) for row in event_rows}) == len(event_rows)
            and len({(row.session_id, row.server_ingest_sequence_no) for row in events}) == len(events)}
    return {'known_fact_integrity_passed': all(checks.values()), 'known_fact_integrity_checks': checks,
        'known_fact_scope': 'Durable emitted event/checkpoint material and actual ACKs only; does not establish a complete workflow or capacity acceptance.',
        'acknowledged_event_count': len(acked_events), 'acknowledged_checkpoint_count': len(acked_checkpoints),
        'unacknowledged_event_count': len(unknown_events),
        'unacknowledged_events_persisted_count': len(unknown_events & set(actual_events)),
        'unacknowledged_events_missing_count': len(unknown_events - set(actual_events)),
        'unacknowledged_checkpoint_count': len(unknown_checkpoints),
        'unacknowledged_checkpoints_persisted_count': len(unknown_checkpoints & set(actual_checkpoints)),
        'unacknowledged_checkpoints_missing_count': len(unknown_checkpoints - set(actual_checkpoints))}


def navigation_receipt_checks(ledger, receipts):
    expected = {token: row for session in ledger['sessions'].values()
                for token, row in session.get('navigation_receipts', {}).items()}
    actual = {str(row.token): row for row in receipts}
    def matches(token):
        stored, planned = actual[token], expected[token]
        receipt = stored.receipt
        return (stored.attempt_id == planned['attempt_id']
            and stored.material_fingerprint == planned['material_fingerprint']
            and receipt.get('navigation_token') == token and receipt.get('committed') is True
            and receipt.get('writer_lease_released') is True
            and receipt.get('to_assignment_id') == planned['to_assignment_id']
            and receipt.get('active_response_event_id') == planned['target_event_id']
            and (not planned.get('acknowledged') or digest(receipt) == planned['receipt_sha256']))
    confirmed = {token for token, row in expected.items() if row.get('acknowledged')}
    known = (len(actual) == len(receipts) and set(actual) <= set(expected) and confirmed <= set(actual)
             and all(matches(token) for token in actual))
    return {'known_navigation_integrity_passed': known,
            'all_navigation_receipts_confirmed_and_matched': known and confirmed == set(expected) == set(actual),
            'navigation_receipt_count': len(actual), 'navigation_receipts_emitted': len(expected),
            'navigation_unknown_receipts_missing': len(set(expected) - confirmed - set(actual))}


def browser_persistence_checks(fixture, ledger, sessions, events, attempts, checkpoints, syncs, visits, submissions, confidence):
    """Compare persisted artifacts with the frozen per-client HTTP ledger, never repair them."""
    session_map = {str(row.pk): row for row in sessions}
    event_map = {str(row.pk): row for row in events}
    attempt_map = {row.pk: row for row in attempts}
    checkpoint_map = {str(row.pk): row for row in checkpoints}
    sync_map = {str(row.pk): row for row in syncs}
    visit_map = {row.pk: row for row in visits}
    confidence_map = {str(row.pk): row for row in confidence}
    expected_checkpoints = {key: (session_id, value) for session_id, row in ledger['sessions'].items()
                            for key, value in row.get('checkpoints', {}).items()}
    expected_syncs = {key: (session_id, value) for session_id, row in ledger['sessions'].items()
                      for key, value in row.get('time_syncs', {}).items()}
    expected_visits = [value for row in ledger['sessions'].values() for value in row.get('visits', [])]
    clients = [row for row in events if row.client_instance_id is not None]
    expected_events = {key: value for row in ledger['sessions'].values() for key, value in row['events'].items()}
    totals = Counter(row.event_type for row in clients)
    per_attempt_counts = Counter((row.attempt_id, row.event_type) for row in clients)
    commits = Counter((row.attempt_id, row.payload.get('submission_token')) for row in clients
                      if row.event_type == 'response_commit_requested')
    all_counts = Counter((row.attempt_id, row.event_type) for row in events)
    ingests, session_attempts, session_visits, tool_events = (defaultdict(list) for _ in range(4))
    for row in events:
        ingests[row.session_id].append(row.server_ingest_sequence_no)
        if row.event_type == 'research_tool':
            tool_events[row.attempt_id].append(row)
    for row in attempts:
        session_attempts[row.session_id].append(row)
    for row in visits:
        session_visits[row.session_id].append(row)
    client_sequences = defaultdict(list)
    page_origins = defaultdict(set)
    for row in clients:
        client_sequences[str(row.client_instance_id)].append(row.client_sequence_no)
        page_origins[row.session_id].add(row.client_time_origin)
    visit_clients = {row['client_instance_id'] for row in expected_visits}
    n = fixture['student_count']
    checks = {
        'browser_workload_ledger_version': ledger.get('workload_version') == BROWSER_ALIGNED_WORKLOAD,
        'browser_twenty_item_frozen_workload': fixture['item_count'] == 20 and len(attempts) == n * 20,
        'browser_unique_full_ingest_ordinals': bool(events) and len(event_map) == len(events) and all(
            sorted(ingests[session.pk])
            == list(range(1, session.next_ingest_sequence_no)) for session in sessions),
        'browser_all_persisted_event_session_student_attempt_bindings': bool(events) and all(
            (session := session_map.get(str(row.session_id))) is not None and row.student_id == session.student_id
            and (row.attempt_id is None or ((attempt := attempt_map.get(row.attempt_id)) is not None
                and attempt.session_id == row.session_id)) for row in events),
        'browser_exact_client_event_envelopes': bool(clients) and all(
            browser_event_envelope_matches(row, expected_events.get(str(row.pk)))
            for row in clients),
        'browser_no_fabricated_input_or_legacy_scenarios': totals['response_transition'] == n * 20
            and totals['response_input_started'] == n * 20 and totals['text_edit_observed'] == n * 8
            and totals['input_operation'] == n * 8
            and not any(row.event_type in ('composition_started', 'composition_updated', 'composition_ended',
                'outbox_recovered', 'network_offline') for row in clients),
        'browser_one_actual_response_and_commit_per_attempt': len(attempts) == n * 20 and all(
            per_attempt_counts[(attempt.pk, 'response_transition')] == 1
            and commits[(attempt.pk, str(attempt.submission_token))] == 1
            for attempt in attempts),
        'browser_exact_immutable_checkpoint_ids_fields_hashes_and_acks': bool(expected_checkpoints)
            and set(checkpoint_map) == set(expected_checkpoints) and len(checkpoint_map) == len(checkpoints)
            and all(session_id in session_map and checkpoint_matches(checkpoint_map[key], value,
                session_map[session_id].student_id) for key, (session_id, value) in expected_checkpoints.items()),
        'browser_exact_clock_sync_ids_documents_and_finalization': len(syncs) == len(expected_syncs) >= n
            and set(sync_map) == set(expected_syncs) and all(time_sync_matches(sync_map[key], value, int(session_id))
                for key, (session_id, value) in expected_syncs.items()),
        'browser_clock_sync_references_owned': bool(clients) and all(
            ((sample := sync_map.get(str(row.time_sync_id))) is not None
             and sample.session_id == row.session_id and sample.client_instance_id == row.client_instance_id)
            if row.time_sync_id is not None else (
                getattr(row, 'trigger', None) == 'resume' or
                (getattr(row, 'event_type', None) in ('pageshow', 'document_visible', 'window_focused')
                 and getattr(row, 'client_sequence_no', 4) <= 3)) for row in [*clients, *checkpoints]),
        'browser_visits_match_authorized_dto_ids': len(visits) == len(expected_visits) == n * 58
            and len({row['visit_id'] for row in expected_visits}) == len(visits),
        'browser_navigation_visit_order_and_render_counts': totals['item_rendered'] == n * 58
            and totals['item_left'] == n * 58 and totals['pageshow'] == n,
        'browser_session_collector_with_continuous_sequence_and_page_clock': len(visit_clients) == n
            and visit_clients == set(client_sequences) == {str(row.client_instance_id) for row in checkpoints}
            and all(sorted(numbers) == list(range(1, len(numbers) + 1)) for numbers in client_sequences.values())
            and len(page_origins) == n and all(len(origins) == 1 for origins in page_origins.values()),
        'browser_checkpoint_trigger_counts': Counter(row.trigger for row in checkpoints)['resume'] == n
            and Counter(row.trigger for row in checkpoints)['navigation'] == n * 38
            and Counter(row.trigger for row in checkpoints)['commit'] == n * 20
            and Counter(row.trigger for row in checkpoints)['session_end'] == n * 20
            and Counter(row.trigger for row in checkpoints)['ack_progress'] >= n * 58,
        'browser_submission_rows_match_final_snapshots_and_tokens': len(submissions) == n * 20,
        'browser_tool_requests_observations_and_server_references': True,
        'browser_confidence_prompt_skip_references_and_final_state': True,
        'browser_completed_session_counters': bool(sessions) and all(row.first_open_count == 20
            and row.submission_count == 20 and row.visit_event_count == 58
            and ledger['sessions'].get(str(row.pk), {}).get('complete') is True for row in sessions),
    }
    for session_id, ledger_row in ledger['sessions'].items():
        ordered = sorted(session_attempts[int(session_id)],
                         key=lambda row: row.assignment.assigned_sequence_index)
        expected_order = [ordered[0].pk] if ordered else []
        for index in range(max(0, len(ordered) - 1)):
            expected_order.extend([ordered[index + 1].pk, ordered[index].pk, ordered[index + 1].pk])
        stored_visits = sorted(session_visits[int(session_id)], key=lambda row: row.event_sequence)
        checks['browser_navigation_visit_order_and_render_counts'] &= (
            [row.attempt_id for row in stored_visits] == expected_order
            and [row.event_sequence for row in stored_visits] == list(range(1, 59)))
        for visit in ledger_row.get('visits', []):
            stored = visit_map.get(visit['visit_id'])
            entered = event_map.get(visit['entry_event_id'])
            checks['browser_visits_match_authorized_dto_ids'] &= (stored is not None and entered is not None
                and entry_matches(entered, visit, session_id)
                and stored.session_id == int(session_id) and stored.attempt_id == visit['attempt_id']
                and stored.visit_index == visit['visit_index'] and entered.event_type == 'item_entered'
                and entered.session_id == int(session_id) and entered.attempt_id == visit['attempt_id']
                and entered.assignment_id == visit['assignment_id']
                and str(entered.correlation_id) == visit['navigation_id'] and entered.payload['visit_id'] == stored.pk)
    for submission in submissions:
        attempt = attempt_map.get(submission.attempt_id)
        target = event_map.get(str(attempt.final_response_event_id)) if attempt else None
        checks['browser_submission_rows_match_final_snapshots_and_tokens'] &= (attempt is not None and target is not None
            and str(submission.submission_token) == str(attempt.submission_token)
            and target.attempt_id == attempt.pk and target.event_type == 'response_transition'
            and str(submission.final_response_snapshot_id) == target.payload.get('after_snapshot_id')
            and submission.final_response_snapshot_id == attempt.final_response_snapshot_id
            and submission.final_answer == attempt.final_answer)
    referenced_tools = set()
    observed_confidence = Counter()
    for row in clients:
        payload = row.payload
        if row.event_type in ('research_tool_requested', 'research_tool_observed'):
            key = payload.get('operation_id') if row.event_type == 'research_tool_requested' else payload.get('server_event_id')
            referenced_tools.add(key)
            source = event_map.get(key)
            match = (source is not None and source.event_type == 'research_tool'
                and source.session_id == row.session_id and source.student_id == row.student_id and source.attempt_id == row.attempt_id
                and all(source.payload.get(field) == payload.get(field) for field in
                        ('tool', 'content_version', 'data', 'initiation_type')))
            if row.event_type == 'research_tool_requested':
                match = match and source.payload.get('stage') == payload.get('stage') and source.payload.get('answer_event_id') == payload.get('answer_event_id')
            target_id = payload.get('answer_event_id')
            target = event_map.get(target_id)
            match = match and (target_id is None or (target is not None and target.attempt_id == row.attempt_id
                and target.event_type == 'response_transition' and payload.get('snapshot_id') == target.payload.get('after_snapshot_id')))
            checks['browser_tool_requests_observations_and_server_references'] &= match
        if row.event_type == 'confidence_observed':
            key = payload.get('confidence_assignment_id')
            assigned = confidence_map.get(key)
            target = event_map.get(payload.get('answer_event_id'))
            checks['browser_confidence_prompt_skip_references_and_final_state'] &= (assigned is not None
                and assigned.attempt_id == row.attempt_id and assigned.condition == 'prompt'
                and assigned.response_status == 'skipped' and assigned.prompt_shown_at is not None
                and assigned.probability is None and payload.get('probability') is None
                and target is not None and target.attempt_id == row.attempt_id)
            observed_confidence[(key, payload.get('stage'))] += 1
    checks['browser_tool_requests_observations_and_server_references'] &= (
        referenced_tools == {str(row.pk) for row in events if row.event_type == 'research_tool'})
    for attempt in attempts:
        config = attempt.session.version.configuration.get('research_tools', {})
        required = [rule for rule in config.get('rules', []) if rule['required'] and rule['tool'] == 'check'
                    and (not rule.get('question_code') or rule['question_code'] == attempt.assignment.snapshot.code)]
        for rule in required:
            checks['browser_tool_requests_observations_and_server_references'] &= any(
                row.attempt_id == attempt.pk and row.event_type == 'research_tool' and row.payload.get('tool') == 'check'
                and row.payload.get('stage') == 'finished' and row.payload.get('data', {}).get('rule_id') == rule['id']
                and row.payload.get('initiation_type') == 'task_required' for row in tool_events[attempt.pk])
        hints = [hint for hint in config.get('hints', []) if hint['question_code'] == attempt.assignment.snapshot.code
                 and not hint['contains_answer']]
        if attempt.assignment.snapshot.question_type == 'fill_blank':
            checks['browser_tool_requests_observations_and_server_references'] &= bool(hints) and any(
                row.attempt_id == attempt.pk and row.event_type == 'research_tool' and row.payload.get('tool') == 'hint'
                and row.payload.get('stage') == 'requested' and row.payload.get('data', {}).get('hint_id') == hints[0]['id']
                for row in tool_events[attempt.pk])
    for assigned in confidence:
        if assigned.condition == 'prompt':
            checks['browser_confidence_prompt_skip_references_and_final_state'] &= (
                observed_confidence[(str(assigned.pk), 'presented')] == observed_confidence[(str(assigned.pk), 'skipped')] == 1
                and all_counts[(assigned.attempt_id, 'confidence_prompt_shown')] == 1
                and all_counts[(assigned.attempt_id, 'confidence_skipped')] == 1)
    return checks


class Command(BaseCommand):
    help = 'Verify ownership, sequence, hashes, acknowledgements and final pointers; kt_e2e only. Does not verify browser input.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--cold-manifest', action='append', default=[])
        parser.add_argument('--workload-version', choices=(CONTRACT, BROWSER_ALIGNED_WORKLOAD), default=CONTRACT)

    def handle(self, *args, **options):
        if connection.settings_dict['NAME'] != 'kt_e2e':
            raise CommandError('Only direct isolated database kt_e2e is permitted')
        path = Path(options['manifest'])
        fixture = json.loads(path.read_text(encoding='utf-8-sig'))
        ledger = json.loads(path.with_suffix('.ledger.json').read_text(encoding='utf-8'))
        if (fixture.get('capture_protocol') != CONTRACT or ledger.get('capture_protocol') != CONTRACT
                or ledger.get('task_id') != fixture.get('task_id')
                or ledger.get('source_config') != fixture.get('source_config')):
            raise CommandError('Dedicated protocol ledger binding required')
        with Path(options['output']).open('x', encoding='utf-8') as output:
            result = self.verify(fixture, ledger, options['cold_manifest'], options.get('workload_version', CONTRACT))
            json.dump(result, output, indent=2)
        self.stdout.write(json.dumps(result))
        if not result['passed']:
            raise CommandError('Capture protocol persistence verification failed')

    def verify(self, fixture, ledger, cold_manifests, workload_version=CONTRACT):
        sessions = list(ExperimentSession.objects.filter(class_task_id=fixture['task_id']).select_related('student'))
        ids = {row.pk for row in sessions}
        expected = {token: (session, values) for session, row in ledger['sessions'].items()
                    for token, values in row['events'].items()}
        stored = list(AssessmentEvent.objects.filter(session_id__in=ids, client_instance_id__isnull=False))
        actual = {str(row.pk): row for row in stored}
        attempt_query = Attempt.objects.filter(session_id__in=ids).select_related('assignment__snapshot')
        if workload_version == BROWSER_ALIGNED_WORKLOAD:
            attempt_query = attempt_query.select_related('session__version')
        attempts = list(attempt_query)
        expected_answers = {int(pk): values for row in ledger['sessions'].values() for pk, values in row.get('answers', {}).items()}
        session_by_id = {str(row.pk): row for row in sessions}
        event_checks = all(token in actual and session in session_by_id
            and event_matches(actual[token], values, session_by_id[session].student_id)
            for token, (session, values) in expected.items())
        answer_checks = all((values := expected_answers.get(row.pk)) is not None
            and row.status == Attempt.Status.SUBMITTED and row.submitted_at is not None
            and str(row.final_response_event_id) == values['target_event_id']
            and str(row.active_response_event_id) == values['target_event_id']
            and str(row.submission_token) == values['submission_token']
            and row.response_version == values['response_version']
            and digest({'question_type': values['question_type'],
                        'value': canonical_response_state(values['question_type'], row.final_answer)}) == values['state_sha256']
            for row in attempts)
        count = fixture['student_count']
        unique_keys = {(row['client_instance_id'], row['client_sequence_no']) for _, row in expected.values()}
        checks = {'ledger_complete': ledger.get('incomplete') is False,
            'all_sessions_owned': len(sessions) == count and len(ids) == count
                and {row.student.username for row in sessions} == set(fixture['usernames'])
                and set(session_by_id) == set(ledger['sessions'])
                and all(ledger['sessions'].get(str(row.pk), {}).get('username_sha256') == digest(row.student.username) for row in sessions),
            'all_terminal': len(sessions) == count and all(row.status == ExperimentSession.Status.COMPLETED and row.terminal_at is not None for row in sessions),
            'zero_missing_or_unexpected_client_events': bool(expected) and set(actual) == set(expected),
            'zero_duplicate_client_events': len(unique_keys) == len(expected) == len(stored),
            'event_ownership_sequence_projection_hash_and_ack_match': bool(expected) and event_checks,
            'all_answers_and_final_targets_persisted': len(attempts) == len(expected_answers) == count * fixture['item_count'] and answer_checks,
            'unique_submissions': len({row['submission_token'] for row in expected_answers.values()}) == len(attempts)
                and SubmissionEvent.objects.filter(attempt__session_id__in=ids).count() == len(attempts),
            'formal_tasks_remain_cold': all(not ExperimentSession.objects.filter(class_task_id=json.loads(Path(path).read_text())['task_id']).exists() for path in cold_manifests)}
        extra = {}
        if workload_version == BROWSER_ALIGNED_WORKLOAD:
            events = list(AssessmentEvent.objects.filter(session_id__in=ids))
            checkpoints = list(ClientCaptureCheckpoint.objects.filter(session_id__in=ids))
            syncs = list(ClientTimeSync.objects.filter(session_id__in=ids))
            visits = list(QuestionVisitEvent.objects.filter(session_id__in=ids))
            submissions = list(SubmissionEvent.objects.filter(attempt__session_id__in=ids))
            confidence = list(ConfidenceAssignment.objects.filter(attempt__session_id__in=ids))
            checks.update(browser_persistence_checks(fixture, ledger, sessions, events, attempts,
                checkpoints, syncs, visits, submissions, confidence))
            extra = {'workload_version': workload_version, 'visit_count': len(visits), 'checkpoint_count': len(checkpoints),
                     'time_sync_count': len(syncs), 'submission_count': len(submissions)}
            extra.update(known_fact_integrity(fixture, ledger, sessions, events, checkpoints, attempts))
            navigation = navigation_receipt_checks(ledger, list(NavigationReceipt.objects.filter(attempt__session_id__in=ids)))
            extra.update(navigation)
            extra['known_fact_integrity_passed'] &= navigation['known_navigation_integrity_passed']
            checks['all_navigation_receipts_confirmed_and_matched'] = navigation['all_navigation_receipts_confirmed_and_matched']
            extra['full_workflow_passed'] = all(checks.values())
        return {'database': 'kt_e2e', 'read_only': True, 'evidence_kind': 'http_protocol_persistence', **extra,
            'browser_dom_ime_offline_verified': False, 'student_count': count, 'session_count': len(sessions),
            'client_event_count': len(stored), 'answer_count': len(attempts), 'checks': checks, 'passed': all(checks.values())}
