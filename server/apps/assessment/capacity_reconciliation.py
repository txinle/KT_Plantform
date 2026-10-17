"""Read-only capacity reconciliation, with retained v2 comparison rules.

Domain/evidence comparisons originate in the isolated 50/180-person reconciler.
The frozen plan, never observed answers, determines expected final material.
Reports contain identifiers/counts/codes only; no passwords, cookies or answers.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json

from django.db import connection, transaction
from django.db.models import Count
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

V3 = 'student-capacity-protocol-v3'
PROTOCOL_VERSIONS = ('student-concurrency-protocol-v2', 'student-capacity-protocol-v1', 'student-capacity-protocol-v2', V3)


def confidence_issues(expected, observed):
    """Compare assigned confidence including real zero and the exact answer head."""
    issues = []
    probability, terminal = expected['probability'], expected['terminal']
    if observed.get('draft_probability') != probability or (terminal and observed.get('probability') != probability):
        issues.append('confidence_probability_differs')
    if probability is not None and str(observed.get('draft_answer_event_id')) != str(expected['target_event_id']):
        issues.append('confidence_answer_binding_differs')
    if expected['condition'] != 'prompt' and probability is not None:
        issues.append('confidence_filled_without_assignment')
    if terminal and expected['condition'] == 'prompt' and observed.get('response_status') != ('skipped' if probability is None else 'committed'):
        issues.append('confidence_final_status_differs')
    return issues

def response_value(attempt, final=False):
    snapshot = attempt.final_response_snapshot if final else attempt.current_response_snapshot
    if snapshot is not None:
        return snapshot.state_json.get('value', snapshot.content) if isinstance(snapshot.state_json, dict) else snapshot.content
    return attempt.final_answer if final else attempt.draft_answer

def _reconcile_domain(rows):
    from django.contrib.auth.hashers import get_hasher, identify_hasher
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    from apps.assessment.models import (ExperimentSession, Attempt, AssessmentEvent, ClientCaptureCheckpoint,
        ConfidenceAssignment, PreparedSessionArtifact, SubmissionEvent, SessionSubmission)
    from apps.accounts.models import User
    authenticated = set()
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        user_id = session.get_decoded().get('_auth_user_id')
        if user_id is not None:
            authenticated.add(str(user_id))
    results = []
    for expected in rows:
        issues, counts = [], {}
        def check(condition, code):
            if not condition:
                issues.append(code)
        check(expected['witness_state'] == 'completed', 'witness_' + expected['witness_state'])
        session = ExperimentSession.objects.filter(pk=expected['session_id']).first()
        if session is None:
            results.append({'account_id': expected['account_id'], 'session_id': expected['session_id'],
                            'issues': issues + ['session_missing'], 'counts': counts})
            continue
        check(session.student_id == expected['user_id'] and session.class_task_id == expected['task_id'], 'session_owner_or_task_differs')
        check(ExperimentSession.objects.filter(student_id=expected['user_id'], class_task_id=expected['task_id']).count() == 1, 'duplicate_effective_session')
        user = User.objects.only('password').filter(pk=expected['user_id']).first()
        check(user is not None, 'planned_user_missing')
        try:
            default_password = user is not None and identify_hasher(user.password).algorithm == get_hasher('default').algorithm
        except ValueError:
            default_password = False
        check(default_password, 'password_hasher_not_default')
        if expected['witness_state'] == 'completed':
            check(str(expected['user_id']) in authenticated, 'no_unexpired_authenticated_database_session')
        terminal = expected['terminal_required']
        check(session.status == ('completed' if terminal else 'prepared' if expected['login_only'] else 'in_progress'), 'session_final_state_differs')
        artifact = PreparedSessionArtifact.objects.filter(session=session).first()
        if not expected['login_only']:
            check(session.started_at is not None, 'activation_missing_start_time')
            check(artifact is not None and bool(artifact.activation_receipt), 'activation_receipt_missing')
            if artifact and artifact.activation_receipt:
                check(artifact.activation_receipt.get('session_id') == session.pk, 'activation_receipt_session_differs')
        else:
            check(session.started_at is None and not (artifact and artifact.activation_receipt), 'login_cohort_activated_early')
        attempts = list(Attempt.objects.filter(session=session).select_related('current_response_snapshot', 'final_response_snapshot'))
        by_assignment = {attempt.assignment_id: attempt for attempt in attempts}
        if expected['startup_only']:
            first = by_assignment.get(expected['first_assignment_id'])
            check(first is not None and first.first_opened_at is not None and first.visit_count >= 1, 'startup_first_visit_missing')
            check(session.first_open_count >= 1 and session.current_assignment_id == expected['first_assignment_id'], 'startup_first_assignment_differs')
            check(session.submission_count == 0 and not SessionSubmission.objects.filter(session=session).exists(), 'startup_unexpected_submission')
        check(len(attempts) == expected['expected_question_count'], 'question_count_differs')
        confidence = {row.attempt_id: row for row in ConfidenceAssignment.objects.filter(attempt__session=session)}
        is_v3 = expected['protocol_version'] == V3
        if not is_v3:
            check(all(row.probability is None and row.draft_probability is None and row.committed_at is None
                      for row in confidence.values()), 'unfilled_confidence_fabricated_on_any_item')
        submissions = {row.attempt_id: row for row in SubmissionEvent.objects.filter(attempt__session=session)}
        if expected['startup_only']:
            check(not submissions, 'startup_unexpected_attempt_submission')
        events = list(AssessmentEvent.objects.filter(session=session).values('event_id', 'attempt_id', 'event_type',
            'client_instance_id', 'client_sequence_no', 'server_ingest_sequence_no', 'payload'))
        event_by_id = {str(row['event_id']): row for row in events}
        saved_ids = [str(saved.get('target_event_id') or saved.get('active_response_event_id') or '')
                     for saved in expected['saved_heads']]
        check(len(saved_ids) == len(set(saved_ids)), 'trajectory_head_reused_for_distinct_saves')
        for saved in expected['saved_heads']:
            head = str(saved.get('target_event_id') or saved.get('active_response_event_id') or '')
            check(head in event_by_id, 'saved_trajectory_head_missing')
            if head in event_by_id:
                attempt = by_assignment.get(saved['assignment_id'])
                check(attempt is not None and event_by_id[head]['attempt_id'] == attempt.pk, 'saved_trajectory_attempt_differs')
        latest_heads = {saved['assignment_id']: saved for saved in expected['saved_heads']}
        for assignment_id, saved in latest_heads.items():
            attempt = by_assignment.get(assignment_id)
            if attempt:
                head = saved.get('target_event_id') or saved.get('active_response_event_id')
                if head:
                    check(str(attempt.active_response_event_id) == str(head), 'latest_saved_response_head_differs')
                if saved.get('response_version') is not None:
                    check(attempt.response_version == saved['response_version'], 'latest_saved_response_version_differs')
        for assignment_id, answer in expected['answers'].items():
            attempt = by_assignment.get(assignment_id)
            check(attempt is not None, 'planned_answer_attempt_missing')
            if attempt is None:
                continue
            check(response_value(attempt) == answer, 'saved_answer_differs')
            if terminal:
                check(attempt.status == 'submitted' and attempt.submitted_at is not None, 'answered_attempt_not_submitted')
                check(response_value(attempt, final=True) == answer, 'final_answer_differs')
                check(attempt.final_response_event_id == attempt.active_response_event_id, 'final_active_response_head_differs')
                check(attempt.pk in submissions and submissions[attempt.pk].final_answer == answer, 'submission_event_answer_differs')
            conf = confidence.get(attempt.pk)
            check(conf is not None, 'answered_confidence_assignment_missing')
            if conf:
                if is_v3:
                    issues.extend(confidence_issues({'probability': expected['confidence'].get(assignment_id),
                        'terminal': terminal, 'condition': conf.condition, 'target_event_id': attempt.active_response_event_id},
                        {'probability': conf.probability, 'draft_probability': conf.draft_probability,
                         'draft_answer_event_id': conf.draft_answer_event_id, 'response_status': conf.response_status}))
                    check(conf.condition == expected['confidence_conditions'].get(assignment_id), 'confidence_assignment_condition_differs')
                else:
                    check(conf.probability is None and conf.draft_probability is None and conf.committed_at is None,
                          'optional_missing_confidence_fabricated_or_zero')
                if terminal and conf.condition == 'prompt' and (not is_v3 or expected['confidence'].get(assignment_id) is None):
                    check(conf.response_status == 'skipped', 'optional_missing_confidence_not_skipped')
                    skipped = [event for event in events if event['attempt_id'] == attempt.pk and event['event_type'] == 'confidence_skipped']
                    check(len(skipped) == 1, 'optional_confidence_skip_event_count_differs')
        for attempt in attempts:
            if attempt.assignment_id not in expected['answers']:
                check(response_value(attempt) in (None, '', [], {}) and attempt.final_answer in (None, '', [], {}),
                      'unplanned_answer_fabricated')
                conf = confidence.get(attempt.pk)
                if is_v3 and conf:
                    check(conf.probability is None and conf.draft_probability is None and conf.committed_at is None,
                          'unplanned_confidence_fabricated')
        if terminal:
            for attempt in attempts:
                if attempt.assignment_id not in expected['answers']:
                    check(attempt.status == 'unanswered' and attempt.final_answer in (None, '', [], {}), 'unanswered_attempt_fabricated_or_wrong_state')
                    check(attempt.is_correct is None and attempt.score is None and attempt.pk not in submissions, 'unanswered_score_or_submission_fabricated')
            check(session.submission_count == len(expected['answers']), 'session_submission_count_differs')
            job = SessionSubmission.objects.filter(session=session).first()
            check(job is not None, 'whole_submission_job_missing')
            if job:
                check(job.status == 'completed' and job.completed_at is not None and session.completed_at is not None,
                      '202_is_not_terminal_completion')
                check(str(job.pk) == expected['submission_id'], 'whole_submission_witness_id_differs')
                if job.completed_at:
                    check(0 <= (job.completed_at - job.accepted_at).total_seconds() <= 300, 'whole_submission_completion_over_300_seconds')
                manifest = {row['attempt_id']: row for row in job.manifest.get('attempts', [])}
                check(set(manifest) == {attempt.pk for attempt in attempts}, 'frozen_submission_manifest_incomplete')
                for attempt in attempts:
                    frozen = manifest.get(attempt.pk, {})
                    check(frozen.get('answer') == expected['answers'].get(attempt.assignment_id), 'frozen_submission_answer_differs')
                    if is_v3:
                        check(frozen.get('confidence_probability') == expected['confidence'].get(attempt.assignment_id),
                              'frozen_confidence_probability_differs')
                        if frozen.get('confidence_probability') is not None:
                            check(frozen.get('confidence_answer_event_id') == str(attempt.final_response_event_id),
                                  'frozen_confidence_answer_binding_differs')
                    else:
                        check(frozen.get('confidence_probability') is None, 'frozen_optional_confidence_fabricated')
                    active_id = str(attempt.active_response_event_id) if attempt.active_response_event_id else None
                    check(frozen.get('target_event_id') == active_id and frozen.get('response_version') == attempt.response_version,
                          'frozen_submission_response_head_or_version_differs')
        checkpoints = list(ClientCaptureCheckpoint.objects.filter(session=session).order_by('server_received_at'))
        clients = defaultdict(list)
        for event in events:
            if event['client_instance_id'] is not None:
                clients[str(event['client_instance_id'])].append(event['client_sequence_no'])
        latest = {str(row.client_instance_id): row for row in checkpoints}
        check(all(row.student_id == expected['user_id'] for row in checkpoints), 'capture_checkpoint_student_differs')
        if not expected['login_only']:
            check(bool(clients), 'capture_client_events_missing')
            check(bool(checkpoints), 'capture_checkpoints_missing')
        for client, sequences in clients.items():
            numbers = sorted(sequences)
            check(bool(numbers) and numbers == list(range(1, max(numbers) + 1)), 'capture_sequence_gap_or_duplicate')
            checkpoint = latest.get(client)
            check(checkpoint is not None, 'capture_client_checkpoint_missing')
            if checkpoint:
                check(checkpoint.pending_event_count == 0 and checkpoint.failed_event_count == 0
                    and not checkpoint.missing_local_sequence_ranges, 'capture_checkpoint_pending_failed_or_missing')
                check(checkpoint.highest_contiguous_acked_sequence_no == checkpoint.highest_emitted_sequence_no,
                      'capture_checkpoint_not_fully_acked')
                check(max(numbers) >= checkpoint.highest_emitted_sequence_no, 'capture_checkpoint_claim_exceeds_database')
        counts.update(attempts=len(attempts), planned_answers=len(expected['answers']),
            answered=sum(a.status == 'submitted' for a in attempts), unanswered=sum(a.status == 'unanswered' for a in attempts),
            capture_events=len(events), client_events=sum(len(v) for v in clients.values()), checkpoints=len(checkpoints),
            confidence_skipped=sum(c.response_status == 'skipped' for c in confidence.values()),
            confidence_committed=sum(c.response_status == 'committed' for c in confidence.values()))
        results.append({'account_id': expected['account_id'], 'session_id': session.pk,
            'witness_state': expected['witness_state'], 'db_session_state': session.status,
            'issues': sorted(set(issues)), 'counts': counts})
    return results

def _time(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')) if value else None

def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def payload_issues(raw, actual, snapshot):
    before_payload, stored = raw['payload'], actual['payload']
    if (raw['event_type'] == actual['event_type'] == 'confidence_observed'
            and raw['event_version'] == actual['event_version'] and raw['event_version'] in (1, 2)):
        if set(stored) != set(before_payload) | {'snapshot_id'} or any(
                _json(stored[key]) != _json(value) for key, value in before_payload.items()):
            return ['confirmed_event_payload_differs']
        reference = before_payload.get('answer_event_id')
        if reference is None:
            return [] if stored['snapshot_id'] is None else ['confirmed_confidence_snapshot_differs']
        head = snapshot['events'].get(reference)
        if (head is None or head['event_type'] != 'response_transition' or head['attempt_id'] != actual['attempt_id']
                or stored['snapshot_id'] != head['payload'].get('after_snapshot_id')):
            return ['confirmed_confidence_snapshot_differs']
        return []
    if not (raw['event_type'] == actual['event_type'] == 'response_transition'
            and raw['event_version'] == actual['event_version'] == 1):
        return [] if _json(before_payload) == _json(stored) else ['confirmed_event_payload_differs']
    generated = {'before_snapshot_id', 'after_snapshot_id'}
    if set(stored) != set(before_payload) | generated or any(
            _json(stored[key]) != _json(value) for key, value in before_payload.items()):
        return ['confirmed_event_payload_differs']
    issues, hashes = [], {}
    attempt = snapshot['attempts'].get(actual['attempt_id'], {})
    for side in ('before', 'after'):
        state = before_payload[f'{side}_state']
        state_hash = hashlib.sha256(_json(state).encode('utf-8')).hexdigest()
        hashes[side] = state_hash
        body = snapshot.get('response_snapshots', {}).get(stored[f'{side}_snapshot_id'])
        if body is None:
            issues.append('confirmed_transition_snapshot_missing')
            continue
        value = state['value']
        content = '' if value is None else value if isinstance(value, str) else _json(value)
        if (body['attempt_id'] != actual['attempt_id'] or _json(body['state_json']) != _json(state)
                or state['question_type'] != attempt.get('question_type')
                or body['state_hash'] != state_hash or before_payload[f'{side}_state_hash'] != state_hash
                or body['content'] != content or body['content_length'] != len(content)
                or body['content_hash'] != hashlib.sha256(content.encode('utf-8')).hexdigest()
                or body['schema_version'] != 1):
            issues.append('confirmed_transition_snapshot_metadata_differs')
    parent_id = before_payload['parent_event_id']
    if parent_id:
        parent = snapshot['events'].get(parent_id)
        if (parent is None or parent['event_type'] != 'response_transition' or parent['attempt_id'] != actual['attempt_id']
                or parent['payload'].get('after_state_hash') != hashes['before']
                or parent['payload'].get('after_snapshot_id') != stored['before_snapshot_id']):
            issues.append('confirmed_transition_parent_differs')
    else:
        state = before_payload['before_state']
        empty = [] if state['question_type'] == 'multiple_choice' else (
            '' if state['question_type'] in ('short_answer', 'fill_blank', 'numeric') else None)
        if _json(state['value']) != _json(empty):
            issues.append('confirmed_transition_root_not_empty')
    if hashes['before'] == hashes['after']:
        issues.append('confirmed_transition_unchanged_state')
    return issues

def audit_confirmed_evidence(expected, evidence, snapshot):
    """Pure comparison of protocol acknowledgements with a read-only DB snapshot."""
    issues, missing_events, missing_checkpoints = [], [], []
    def check(condition, code):
        if not condition:
            issues.append(code)
    check(snapshot['password_cost_current'], 'password_hash_cost_not_current_default')
    emitted = {row['event_id']: row for row in evidence.get('capture_events', [])}
    acknowledgements = evidence.get('event_acknowledgements', [])
    checkpoint_rows = {row['checkpoint_id']: row for row in evidence.get('capture_checkpoints', [])}
    checkpoint_ids = evidence.get('checkpoint_acknowledgements', [])
    client = evidence.get('client_instance_id')
    for ack in acknowledgements:
        event_id = ack['event_id']
        raw, actual = emitted.get(event_id), snapshot['events'].get(event_id)
        check(raw is not None, 'confirmed_event_source_missing')
        if actual is None:
            issues.append('confirmed_event_missing')
            missing_events.append(event_id)
            continue
        check(actual['session_id'] == expected['session_id'] and actual['student_id'] == expected['user_id'], 'confirmed_event_owner_differs')
        check(str(actual['client_instance_id']) == client and actual['client_sequence_no'] == ack['client_sequence_no']
              and actual['server_ingest_sequence_no'] == ack['server_ingest_sequence_no'], 'confirmed_event_ack_differs')
        if raw:
            issues.extend(payload_issues(raw, actual, snapshot))
            check(all(actual.get(field) == raw.get(field) for field in ('event_type', 'event_version', 'attempt_id', 'client_sequence_no', 'app_version')),
                  'confirmed_event_envelope_differs')
            if expected.get('driver', 'protocol') == 'protocol':
                check(raw.get('app_version') == expected['protocol_version'], 'protocol_event_version_differs')
    for checkpoint_id in checkpoint_ids:
        raw, actual = checkpoint_rows.get(checkpoint_id), snapshot['checkpoints'].get(checkpoint_id)
        check(raw is not None, 'confirmed_checkpoint_source_missing')
        if actual is None:
            issues.append('confirmed_checkpoint_missing')
            missing_checkpoints.append(checkpoint_id)
            continue
        check(actual['session_id'] == expected['session_id'] and actual['student_id'] == expected['user_id']
              and str(actual['client_instance_id']) == client, 'confirmed_checkpoint_owner_differs')
        if raw:
            fields = ('highest_emitted_sequence_no', 'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no',
                      'pending_event_count', 'failed_event_count', 'missing_local_sequence_ranges', 'trigger')
            check(all(actual.get(field) == raw[field] for field in fields if field in raw), 'confirmed_checkpoint_content_differs')
    if evidence.get('status') == 'completed':
        check(set(emitted) == {row['event_id'] for row in acknowledgements}, 'protocol_emitted_events_not_all_confirmed')
        check(set(checkpoint_rows) == set(checkpoint_ids), 'protocol_checkpoints_not_all_confirmed')
    if not expected['login_only']:
        check(snapshot['activation_session_id'] == expected['session_id'] and snapshot['session_started_count'] == 1,
              'protocol_activation_receipt_or_start_count_differs')
        check(bool(evidence.get('visits')), 'protocol_entry_receipts_missing')
        check(bool(evidence.get('lease_acquisitions')), 'protocol_lease_acquisitions_missing')
        activation = evidence.get('activation_receipt', {})
        check(activation.get('session_id') == expected['session_id'] and activation.get('task_id') == expected.get('task_id'),
              'protocol_activation_response_binding_differs')
        entry_id = activation.get('entry_receipt', {}).get('entry_event_id')
        check(entry_id in snapshot['events'] and snapshot['events'][entry_id]['event_type'] == 'item_entered',
              'protocol_activation_entry_receipt_missing')
    for receipt in evidence.get('visits', []):
        event = snapshot['events'].get(receipt['entry_event_id'])
        visit = snapshot['visits'].get(str(receipt['visit_id']))
        check(event is not None and event['session_id'] == expected['session_id'] and event['event_type'] == 'item_entered'
              and str(event.get('correlation_id')) == receipt['navigation_id']
              and event['attempt_id'] == receipt['attempt_id'] and event['payload'].get('visit_id') == receipt['visit_id'],
              'protocol_entry_event_differs')
        check(visit is not None and visit['attempt_id'] == receipt['attempt_id'] and visit['visit_index'] == receipt['visit_index'],
              'protocol_visit_row_differs')
    ack_ids = {row['event_id'] for row in acknowledgements}
    if expected['protocol_version'] == V3:
        for index, action in enumerate(expected.get('save_schedule', [])):
            samples = evidence.get('save_samples', [])
            sample = samples[index] if index < len(samples) else {}
            raw = emitted.get(sample.get('event_id'), {})
            if sample.get('event_id'):
                check(raw.get('event_type') in ('response_transition', 'choice_state_changed'), 'protocol_planned_action_event_missing')
                payload = raw.get('payload', {})
                actual_value = payload.get('after_state', {}).get('value') if raw.get('event_type') == 'response_transition' else payload.get('value')
                check(actual_value == action['value'], 'protocol_planned_action_value_differs')
                if evidence.get('status') == 'completed':
                    check(sample['event_id'] in ack_ids, 'protocol_planned_action_not_confirmed')
            elif evidence.get('status') == 'completed':
                check(False, 'protocol_planned_action_event_missing')
    for row in evidence.get('expected_attempts', []):
        actual = snapshot['attempts'].get(row['attempt_id'])
        check(actual is not None, 'protocol_final_attempt_missing')
        if actual:
            active_id = str(actual['active_response_event_id']) if actual.get('active_response_event_id') else None
            check(actual['assignment_id'] == row['assignment_id'] and actual['response_version'] == row['response_version']
                  and active_id == row['target_event_id'], 'protocol_attempt_final_head_or_version_differs')
        for event_id in row.get('transition_event_ids', []):
            event = snapshot['events'].get(event_id)
            check(event_id in ack_ids and event is not None and event['attempt_id'] == row['attempt_id'],
                  'protocol_transition_history_not_confirmed_or_missing')
    acquired = defaultdict(list)
    released = defaultdict(list)
    for collection, grouped in [('lease_acquisitions', acquired), ('lease_releases', released)]:
        for lease in evidence.get(collection, []):
            check(lease.get('confirmed') is True and lease.get('client_instance_id') == client, 'protocol_lease_confirmation_differs')
            grouped[lease['attempt_id']].append(lease)
    for attempt_id, leases in acquired.items():
        actual = snapshot['attempts'].get(attempt_id)
        check(actual is not None, 'protocol_lease_attempt_missing')
        if actual is None:
            continue
        owner, expires, token = actual['writer_client_instance_id'], actual['writer_lease_expires_at'], actual['writer_lease_token']
        check(all(value is None for value in (owner, expires, token)) or all(value is not None for value in (owner, expires, token)),
              'protocol_lease_database_state_incomplete')
        observations = [(row.get('operation_index', 0), 'acquired', row) for row in leases]
        observations += [(row.get('operation_index', 0), 'released', row) for row in released[attempt_id]]
        _, last_kind, last = max(observations, key=lambda value: value[0])
        if owner is not None:
            check(str(owner) == client, 'protocol_lease_final_owner_differs')
            if not expected['terminal_required']:
                check(last_kind == 'acquired' and _time(expires) >= _time(last['expires_at']), 'protocol_lease_final_state_differs')
        elif not expected['terminal_required']:
            check(last_kind == 'released', 'protocol_lease_clear_without_matching_release')
    return {'issues': sorted(set(issues)), 'missing_event_ids': missing_events, 'missing_checkpoint_ids': missing_checkpoints,
            'counts': {'confirmed_events': len(acknowledgements), 'confirmed_checkpoints': len(checkpoint_ids),
                       'confirmed_visits': len(evidence.get('visits', [])), 'lease_acquisitions': sum(map(len, acquired.values()))}}

def database_snapshot(expected):
    from django.contrib.auth.hashers import get_hasher
    from apps.accounts.models import User
    from apps.assessment.models import (AssessmentEvent, ClientCaptureCheckpoint, Attempt, PreparedSessionArtifact,
                                        QuestionVisitEvent, ResponseSnapshot)
    events = list(AssessmentEvent.objects.filter(session_id=expected['session_id']).values(
        'event_id', 'event_type', 'event_version', 'session_id', 'student_id', 'attempt_id', 'client_instance_id',
        'client_sequence_no', 'server_ingest_sequence_no', 'correlation_id', 'payload', 'app_version', 'time_sync_id',
        'client_wall_time_utc', 'client_monotonic_ms', 'client_time_origin', 'quality_flags',
        'server_received_at', 'server_committed_at', 'interval_quality'))
    checkpoints = list(ClientCaptureCheckpoint.objects.filter(session_id=expected['session_id']).values(
        'checkpoint_id', 'session_id', 'student_id', 'client_instance_id', 'highest_emitted_sequence_no',
        'highest_acked_sequence_no', 'highest_contiguous_acked_sequence_no', 'pending_event_count', 'failed_event_count',
        'missing_local_sequence_ranges', 'trigger'))
    artifact = PreparedSessionArtifact.objects.filter(session_id=expected['session_id']).first()
    user = User.objects.only('password').filter(pk=expected['user_id']).first()
    return {'events': {str(row['event_id']): row for row in events},
        'checkpoints': {str(row['checkpoint_id']): row for row in checkpoints},
        'visits': {str(row['id']): row for row in QuestionVisitEvent.objects.filter(session_id=expected['session_id']).values('id', 'attempt_id', 'visit_index')},
        'attempts': {row['id']: {**row, 'question_type': row['assignment__snapshot__question_type']}
                    for row in Attempt.objects.filter(session_id=expected['session_id']).values(
            'id', 'assignment_id', 'response_version', 'active_response_event_id',
            'writer_client_instance_id', 'writer_lease_expires_at', 'writer_lease_token', 'assignment__snapshot__question_type')},
        'response_snapshots': {str(row['snapshot_id']): row for row in ResponseSnapshot.objects.filter(
            attempt__session_id=expected['session_id']).values('snapshot_id', 'attempt_id', 'state_json', 'state_hash',
                'content', 'content_hash', 'content_length', 'schema_version')},
        'password_cost_current': user is not None and not get_hasher('default').must_update(user.password),
        'activation_session_id': artifact.activation_receipt.get('session_id') if artifact else None,
        'session_started_count': sum(row['event_type'] == 'session_started' for row in events)}


def adapt_inputs(plan, fixture, records):
    if plan.get('version') not in PROTOCOL_VERSIONS or len(plan.get('roster', [])) != plan.get('count'):
        raise ValueError('Protocol version or frozen denominator differs')
    is_v3 = plan.get('version') == V3
    if is_v3 and (fixture.get('fixture_id') != fixture.get('plan_sha')
            or hashlib.sha256(_json(fixture.get('fixture_spec')).encode()).hexdigest() != fixture.get('plan_sha')
            or fixture.get('fixture_spec', {}).get('content_binding_sha256')
                != hashlib.sha256(_json(fixture['accounts']).encode()).hexdigest()):
        raise ValueError('Full baseline account material binding differs')
    matching_source = (fixture.get('source_ownership', {}).get('command') == 'prepare_student_capacity_baseline'
        and fixture.get('source_fixture_id', fixture.get('run_id')) == plan.get('source_fixture_id')
        and bool(fixture.get('baseline_snapshot_id'))
        and fixture.get('baseline_snapshot_id') == plan.get('baseline_snapshot_id')) if is_v3 else (
        fixture.get('source_ownership', {}).get('command') == 'seed_prepared_startup_load'
        and fixture.get('run_id') == plan.get('run_id'))
    if (fixture.get('isolated_demo') is not True or fixture.get('database') != 'kt_e2e' or not matching_source):
        raise ValueError('Only the matching isolated fixture is permitted')
    accounts = {a['account_id']: a for a in fixture['accounts']}
    grouped = defaultdict(list)
    for record in records:
        grouped[record.get('account_id')].append(record)
    if len({row['account_id'] for row in plan['roster']}) != plan['count']:
        raise ValueError('Duplicate planned denominator')
    outcomes = []
    for planned in plan['roster']:
        account = accounts[planned['account_id']]
        if any(planned[key] != account[source] for key, source in (
                ('user_id', 'user_id'), ('task_id', 'taskId'), ('session_id', 'session_id'), ('course_id', 'courseId'))):
            raise ValueError('Frozen fixture ownership differs')
        schedule = planned.get('save_schedule', [])
        final = {row['assignment_id']: row['value'] for row in schedule}
        declared = {a['assignment_id']: a['value'] for a in planned['answers'] if a.get('value') is not None}
        if final != declared:
            raise ValueError('Frozen final answers and save schedule differ')
        if not is_v3 and any(a.get('confidence_probability') is not None for a in planned['answers']):
            raise ValueError('Protocol freezes optional confidence as unfilled')
        if is_v3 and any(a.get('confidence_probability') is not None and a.get('confidence_condition') != 'prompt' for a in planned['answers']):
            raise ValueError('Confidence requires frozen server assignment')
        if is_v3:
            bindings = {q['assignment_id']: q for q in account['questions']}
            if any(a['assignment_id'] not in bindings or a['attempt_id'] != bindings[a['assignment_id']]['attempt_id']
                    or a.get('confidence_condition', 'not_assigned') != bindings[a['assignment_id']].get('confidence_condition', 'not_assigned')
                    for a in planned['answers']):
                raise ValueError('Frozen question or confidence ownership differs')
        observed = grouped[planned['account_id']]
        witness = observed[0] if len(observed) == 1 else {}
        state = witness.get('status', 'missing' if not observed else 'duplicate')
        if witness and (witness.get('run_id') != plan['run_id'] or witness.get('plan_id') != plan['plan_id']):
            state = 'binding_mismatch'
        issues = []
        terminal = planned.get('terminal_required', planned['role'] in ('submission', 'full-flow')) if is_v3 else planned['role'] in ('submission', 'full-flow')
        if witness and any(witness.get(key) != planned[key] for key in ('user_id', 'task_id', 'session_id', 'course_id', 'role')):
            issues.append('protocol_record_ownership_differs')
        if is_v3 and witness and witness.get('driver', 'protocol') != planned.get('driver', 'protocol'):
            issues.append('protocol_record_driver_differs')
        heads = witness.get('expected_attempts', [])
        if state == 'completed':
            expected_ids = {q['assignment_id']: q['attempt_id'] for q in account['questions']}
            if len(heads) != len(expected_ids) or {r['assignment_id']: r['attempt_id'] for r in heads} != expected_ids:
                issues.append('protocol_expected_attempts_incomplete')
            for row in heads:
                answer = row.get('answer')
                if (row['assignment_id'] in final and answer != final[row['assignment_id']]) or (row['assignment_id'] not in final and answer not in (None, '', [], {})):
                    issues.append('protocol_final_answer_differs')
                if is_v3:
                    planned_answer = next((a for a in planned['answers'] if a['assignment_id'] == row['assignment_id']), {})
                    if row.get('confidence_probability') != planned_answer.get('confidence_probability'):
                        issues.append('protocol_confidence_probability_differs')
                elif row.get('confidence_probability') is not None:
                    issues.append('protocol_optional_confidence_not_null')
            samples = witness.get('save_samples', [])
            if is_v3:
                ids = [s.get('event_id') for s in samples if s.get('event_id')]
                if len(ids) != len(set(ids)):
                    issues.append('protocol_planned_action_event_reused')
            if (len(samples) != len(schedule) or any(sample.get('schedule_index') != index
                    or sample.get('assignment_id') != action['assignment_id']
                    or sample.get('status') not in (('acknowledged', 'superseded') if is_v3 else ('acknowledged',))
                    for index, (sample, action) in enumerate(zip(samples, schedule)))):
                issues.append('protocol_planned_saves_not_all_confirmed')
            if terminal and witness.get('final_status') != 'completed':
                issues.append('protocol_202_is_not_completed')
        outcomes.append({'account_id': planned['account_id'], 'user_id': planned['user_id'],
            'session_id': planned['session_id'], 'task_id': planned['task_id'], 'protocol_version': plan['version'],
            'witness_state': state, 'terminal_required': terminal,
            'login_only': planned['role'] == 'login' and plan['mode'] in ('login', 'login-mixed', 'entry-mixed'),
            'startup_only': planned['role'] in ('startup', 'startup-mixed'),
            'first_assignment_id': planned['answers'][0]['assignment_id'] if planned['answers'] else None,
            'answers': final, 'saved_heads': [{'assignment_id': r['assignment_id'], 'response_version': r['response_version'],
                'target_event_id': r['target_event_id']} for r in heads if r.get('target_event_id')],
            'submission_id': witness.get('submission_id'), 'expected_question_count': len(account['questions']),
            'driver': planned.get('driver', 'protocol'), 'save_schedule': schedule,
            'confidence': {a['assignment_id']: a.get('confidence_probability') for a in planned['answers']},
            'confidence_conditions': {a['assignment_id']: a.get('confidence_condition', 'not_assigned') for a in planned['answers']},
            'witness': witness, 'input_issues': issues})
    return outcomes, sum(len(rows) for account, rows in grouped.items() if account not in {r['account_id'] for r in outcomes})


def projection_issues(snapshot, *, revision, projection_version):
    issues = []
    for valid, code in (
            (snapshot.get('source_revision') == revision, 'statistics_source_revision_stale'),
            (snapshot.get('projection_version') == projection_version, 'statistics_projection_version_stale'),
            (snapshot.get('status') == 'succeeded' and snapshot.get('is_current'), 'statistics_current_missing'),
            (snapshot.get('input_status') == 'complete' and snapshot.get('coverage_exists'), 'statistics_input_not_complete'),
            (snapshot.get('event_high_watermark') == snapshot.get('expected_event_high_watermark'), 'statistics_event_watermark_differs'),
            (snapshot.get('response_rows_match'), 'statistics_response_rows_differ'),
            (snapshot.get('visit_rows_match'), 'statistics_visit_rows_differ'),
            (snapshot.get('behavior_rows_match'), 'statistics_behavior_rows_differ'),
            (snapshot.get('export_readable'), 'statistics_export_contract_unverified')):
        if not valid:
            issues.append(code)
    finished, began = snapshot.get('finished_at'), snapshot.get('ready_after')
    if not (finished and began and 0 <= (finished - began).total_seconds() <= 300):
        issues.append('statistics_not_ready_within_300_seconds')
    return issues


def statistics_snapshot(session):
    from apps.analytics.models import RecordingCoverageSession, ResearchSessionMaterialization
    from apps.analytics.research_dataset import read_research_bundle
    from apps.analytics.research_materialization import _scope_for_session
    item = ResearchSessionMaterialization.objects.filter(session=session, is_current=True).first()
    if item is None:
        return {'status': 'missing', 'input_status': None, 'export_readable': False}
    completeness = item.session_summary.get('input_completeness', {})
    ready_after = max((value for value in (session.completed_at, session.authoritative_terminal_at,
        session.research_source_changed_at) if value is not None), default=None)
    snapshot = {'source_revision': item.source_revision, 'projection_version': item.projection_version,
        'status': item.status, 'is_current': item.is_current, 'input_status': completeness.get('status'),
        'coverage_exists': RecordingCoverageSession.objects.filter(run_id=completeness.get('coverage_run_id'),
            session=session, overall_status='complete').exists(),
        'finished_at': item.finished_at, 'ready_after': ready_after, 'event_high_watermark': item.event_high_watermark,
        'expected_event_high_watermark': max(0, session.next_ingest_sequence_no - 1), 'export_readable': False}
    # Rebuild authorized source rows without creating snapshots, coverage or export jobs.
    # Compare contract rows, never raw-event counts against exported-row counts.
    try:
        with transaction.atomic():
            bundle, _ = read_research_bundle(_scope_for_session(session), include_course_catalog=False)
        snapshot['response_rows_match'] = list(item.response_rows.order_by('ordinal').values_list('row', flat=True)) == bundle['files']['responses']
        snapshot['visit_rows_match'] = list(item.visit_rows.order_by('ordinal').values_list('row', flat=True)) == [r.get('row', []) for r in bundle['visits']]
        snapshot['behavior_rows_match'] = list(item.behavior_rows.order_by('ordinal').values_list('row', flat=True)) == [r.get('row', []) for r in bundle['behavior']]
        snapshot['export_readable'] = len(bundle['sessions']) == 1 and len(bundle['responses']) == session.attempts.count()
        snapshot['row_counts_match'] = item.row_counts == {key: len(bundle[source]) for key, source in (
            ('responses', 'responses'), ('visits', 'visits'), ('behavior', 'behavior'))}
    except (ValueError, TypeError, LookupError, KeyError):
        snapshot['export_readable'] = False
    return snapshot


def _additional_evidence(expected, snapshot):
    from .models import ClientTimeSync
    issues, evidence = [], expected['witness']
    client = evidence.get('client_instance_id')
    emitted = {r['event_id']: r for r in evidence.get('capture_events', [])}
    actual_ids = {key for key, event in snapshot['events'].items()
                  if str(event['client_instance_id']) == client and event['client_sequence_no'] is not None}
    if actual_ids - set(emitted):
        issues.append('protocol_database_client_events_not_all_witnessed')
    for raw in emitted.values():
        actual = snapshot['events'].get(raw['event_id'])
        if actual and str(actual.get('time_sync_id') or '') != str(raw.get('time_sync_id') or ''):
            issues.append('protocol_event_time_sync_differs')
    for row in evidence.get('time_syncs', []):
        confirmed = row.get('confirmed', {})
        sync = ClientTimeSync.objects.filter(pk=confirmed.get('time_sync_id')).first()
        if not (sync and sync.session_id == expected['session_id'] and str(sync.client_instance_id) == client
                and sync.sync_quality == confirmed.get('sync_quality') == 'valid'):
            issues.append('protocol_time_sync_missing_or_differs')
    return issues


def _requires_safety_stop(rows, outcomes, statistics, unknown):
    # Failed or missing workloads remain measured failures; they alone do not
    # establish material loss. Ambiguous identities cannot bound the next run.
    if unknown or len(rows) != len(outcomes):
        return True
    confirmed_codes = {
        'duplicate_effective_submission', 'session_owner_or_task_differs', 'protocol_record_ownership_differs',
        'protocol_database_client_events_not_all_witnessed', 'protocol_time_sync_missing_or_differs',
        'protocol_event_time_sync_differs', 'protocol_transition_history_not_confirmed_or_missing',
    }
    for expected, result in zip(rows, outcomes):
        state, issues = expected['witness_state'], result['issues']
        if state not in ('completed', 'failed', 'missing'):
            return True
        if state == 'completed' and issues:
            return True
        if confirmed_codes.intersection(issues) or any(code.startswith('confirmed_') for code in issues):
            return True
    row_codes = {'statistics_response_rows_differ', 'statistics_visit_rows_differ',
                 'statistics_behavior_rows_differ', 'statistics_row_counts_differ'}
    stale_codes = {'statistics_source_revision_stale', 'statistics_projection_version_stale',
                   'statistics_current_missing'}
    return any(row_codes.intersection(result['issues']) and not stale_codes.intersection(result['issues'])
               for result in statistics)


def independent_evidence(expected, session, snapshot, statistics):
    """Selected-cohort source facts survive the next isolated baseline restore."""
    from .models import ConfidenceAssignment, SessionSubmission, SubmissionEvent, ClientTimeSync
    from apps.analytics.models import ResearchSessionMaterialization
    database = dict(snapshot)
    database['attempts'] = {key: {**{k: v for k, v in value.items() if k != 'writer_lease_token'},
                                'writer_lease_token_present': value.get('writer_lease_token') is not None}
                            for key, value in snapshot['attempts'].items()}
    database['confidence'] = list(ConfidenceAssignment.objects.filter(attempt__session=session).values())
    database['whole_submissions'] = list(SessionSubmission.objects.filter(session=session).values(
        'id', 'status', 'submission_token', 'manifest', 'accepted_at', 'completed_at', 'error_code'))
    database['item_submissions'] = list(SubmissionEvent.objects.filter(attempt__session=session).values())
    database['time_syncs'] = list(ClientTimeSync.objects.filter(session=session).values())
    database['session'] = {'id': session.pk, 'status': session.status, 'submission_count': session.submission_count,
        'completed_at': session.completed_at, 'research_source_revision': session.research_source_revision,
        'event_high_watermark': max(0, session.next_ingest_sequence_no - 1)}
    projection = ResearchSessionMaterialization.objects.filter(session=session, is_current=True).first()
    if projection:
        database['projection'] = {'id': projection.pk, 'source_revision': projection.source_revision,
            'projection_version': projection.projection_version, 'status': projection.status,
            'event_high_watermark': projection.event_high_watermark, 'session_summary': projection.session_summary,
            'responses': list(projection.response_rows.order_by('ordinal').values_list('row', flat=True)),
            'visits': list(projection.visit_rows.order_by('ordinal').values_list('row', flat=True)),
            'behavior': list(projection.behavior_rows.order_by('ordinal').values_list('row', flat=True))}
    return {'account_id': expected['account_id'], 'session_id': session.pk, 'driver': expected.get('driver', 'protocol'),
            'database': database, 'statistics': statistics, 'read_only': True, 'isolation': 'repeatable_read'}


def reconcile_capacity(plan, fixture, records, *, require_statistics=True, evidence_output=None):
    from apps.analytics.models import ResearchSessionMaterialization
    from apps.analytics.research_materialization import PROJECTION_VERSION
    from .models import ExperimentSession, SubmissionEvent
    name = connection.settings_dict['NAME']
    if connection.vendor != 'postgresql' or not (name == 'kt_e2e' or name.startswith('test_') or (name.startswith('kt_') and name.endswith('_test'))):
        raise ValueError('Only an explicitly isolated PostgreSQL database is permitted')
    rows, unknown = adapt_inputs(plan, fixture, records)
    statistics = []
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        outcomes = _reconcile_domain(rows)
        for expected, result in zip(rows, outcomes):
            session = ExperimentSession.objects.select_related('version__experiment__course').filter(pk=expected['session_id']).first()
            issues = result['issues'] + expected['input_issues']
            if session is not None:
                stats = None
                # Empty login/startup streams need no fabricated browser evidence.
                if not expected['answers'] and not expected['witness'].get('capture_events'):
                    issues = [code for code in issues if code not in ('capture_client_events_missing', 'capture_checkpoints_missing')]
                snapshot = database_snapshot(expected)
                try:
                    audited = audit_confirmed_evidence(expected, expected['witness'], snapshot)
                    issues += audited['issues'] + _additional_evidence(expected, snapshot)
                    result['protocol_evidence'] = {key: value for key, value in audited.items() if key != 'issues'}
                except (KeyError, TypeError, ValueError):
                    issues.append('protocol_evidence_invalid')
                if SubmissionEvent.objects.filter(attempt__session=session).values('attempt_id').annotate(n=Count('pk')).filter(n__gt=1).exists():
                    issues.append('duplicate_effective_submission')
                if session.status in ('completed', 'expired') and require_statistics:
                    stats = statistics_snapshot(session)
                    stat_issues = projection_issues(stats, revision=session.research_source_revision, projection_version=PROJECTION_VERSION)
                    if stats.get('row_counts_match') is False:
                        stat_issues.append('statistics_row_counts_differ')
                    statistics.append({'session_id': session.pk, 'issues': stat_issues,
                        'input_status': stats.get('input_status'), 'source_revision': session.research_source_revision,
                        'materialization_revision': stats.get('source_revision'),
                        'failed': ResearchSessionMaterialization.objects.filter(session=session, source_revision=session.research_source_revision,
                            projection_version=PROJECTION_VERSION, status='failed').exists()})
                if evidence_output is not None:
                    durable = independent_evidence(expected, session, snapshot, stats)
                    durable.update(plan_id=plan['plan_id'], run_id=plan['run_id'], protocol_version=plan['version'],
                        source_fixture_id=plan.get('source_fixture_id'), baseline_snapshot_id=plan.get('baseline_snapshot_id'))
                    evidence_output.write(json.dumps(durable, cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False) + '\n')
                    evidence_output.flush()
            result['issues'] = sorted(set(issues))
    witness = Counter(row['witness_state'] for row in rows)
    clean = sum(not r['issues'] for r in outcomes)
    workflow_complete = witness.get('completed', 0) == len(rows)
    safety_stop = _requires_safety_stop(rows, outcomes, statistics, unknown)
    eligible = len(statistics)
    stats = {'eligible_count': eligible, 'latest_complete_count': sum(not [code for code in row['issues'] if code != 'statistics_not_ready_within_300_seconds'] for row in statistics),
        'failed_count': sum(row['failed'] for row in statistics),
        'latest_within_300s': all('statistics_not_ready_within_300_seconds' not in row['issues'] for row in statistics),
        'all_materials_complete': all(row['input_status'] == 'complete' for row in statistics)}
    integrity_passed = clean == len(rows) and not unknown
    return {'version': 'student-capacity-db-reconciliation-v1', 'plan_id': plan['plan_id'], 'run_id': plan['run_id'],
        'mode': plan['mode'], 'planned': len(rows),
        'driver_denominators': dict(Counter(row.get('driver', 'protocol') for row in rows)),
        'clean': clean, 'with_differences': len(rows) - clean,
        'integrity_passed': integrity_passed, 'integrity_verified': integrity_passed,
        'workflow_complete': workflow_complete, 'safety_stop': safety_stop,
        'witness_counts': dict(witness), 'unknown_record_count': unknown,
        'issue_counts': dict(Counter(code for r in outcomes for code in r['issues'])), 'outcomes': outcomes,
        'expected_terminal_count': sum(row['terminal_required'] for row in rows),
        'statistics_required': require_statistics, 'stats': stats,
        'stats_ready': stats['latest_complete_count'] == eligible and stats['failed_count'] == 0
            and stats['latest_within_300s'] and stats['all_materials_complete'],
        'statistics_outcomes': statistics, 'read_only': True, 'isolation': 'repeatable_read', 'capacity_verdict': None,
        'limitations': ['Synthetic HTTP events do not establish browser rendering or physical client operability.',
            'Missing/failed witness evidence is not proof of data loss and remains in the full planned denominator.',
            'Source export-reader rows are compared with current projection rows; no frozen export archive is created or certified.',
            'This read-only snapshot does not establish capacity or two-repeat performance acceptance.']}
