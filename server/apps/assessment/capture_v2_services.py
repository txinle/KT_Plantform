"""Append-only short-response evidence. Saving an answer is a separate action."""
from copy import deepcopy
import uuid

from django.db import transaction
from django.core.exceptions import ValidationError

from .event_batch_services import ProcessConflictError, ProcessRequestError, append_event_batch, batch_session_contexts
from .models import AssessmentEvent, ExperimentSession
from .response_snapshot_services import (
    ResponseState, canonical_response_state, create_response_snapshot, response_snapshot_content,
)


def _checked_state(attempt, payload, side):
    document = payload[f'{side}_state']
    kind = attempt.assignment.snapshot.question_type
    value = document['value']
    if document['question_type'] != kind or canonical_response_state(kind, value) != value:
        raise ProcessRequestError('invalid_response_state')
    options = {str(option['key']) for option in attempt.assignment.snapshot.options}
    if kind == 'single_choice' and value is not None and value not in options:
        raise ProcessRequestError('unknown_response_option')
    if kind == 'multiple_choice' and (not isinstance(value, list) or not set(value) <= options):
        raise ProcessRequestError('unknown_response_option')
    if kind in {'short_answer', 'fill_blank', 'numeric'} and not isinstance(value, str):
        raise ProcessRequestError('text_response_required')
    state = ResponseState(kind, value)
    if state.state_hash != payload[f'{side}_state_hash']:
        raise ProcessRequestError('response_hash_mismatch')
    return create_response_snapshot(
        attempt=attempt, content=response_snapshot_content(value), response_state=state,
    )


def prepare_input_operation(parsed):
    if not uses_capture_v2(parsed.session):
        raise ProcessRequestError('capture_contract_mismatch')
    parent_id = parsed.payload['parent_event_id']
    if parent_id and not AssessmentEvent.objects.filter(
        pk=parent_id, attempt=parsed.attempt, event_type='response_transition',
    ).exists():
        raise ProcessRequestError('parent_not_received', status=409)
    return parsed.payload


def prepare_transition(parsed):
    """Validate references and materialize immutable bodies inside the batch transaction."""
    payload = deepcopy(parsed.payload)
    if not uses_capture_v2(parsed.session):
        raise ProcessRequestError('capture_contract_mismatch')
    before = _checked_state(parsed.attempt, payload, 'before')
    after = _checked_state(parsed.attempt, payload, 'after')
    if before.state_hash == after.state_hash:
        raise ProcessRequestError('unchanged_response')
    parent_id = payload['parent_event_id']
    if parent_id:
        parent = AssessmentEvent.objects.filter(
            pk=parent_id, attempt=parsed.attempt, event_type='response_transition',
        ).first()
        if parent is None:
            raise ProcessRequestError('parent_not_received', status=409)
        if parent.payload['after_state_hash'] != before.state_hash:
            raise ProcessConflictError('response_parent_mismatch')
    else:
        empty = [] if before.state_json['question_type'] == 'multiple_choice' else (
            '' if before.state_json['question_type'] in {'short_answer', 'fill_blank', 'numeric'} else None
        )
        if before.state_json['value'] != empty:
            raise ProcessRequestError('response_root_not_empty')
    for side, snapshot in [('before', before), ('after', after)]:
        supplied = payload.get(f'{side}_snapshot_id')
        if supplied is not None and supplied != str(snapshot.pk):
            raise ProcessRequestError('response_snapshot_mismatch')
        payload[f'{side}_snapshot_id'] = str(snapshot.pk)
    return payload


@transaction.atomic
def ingest_capture_batch(*, student, document, request_received_at=None, locked_sessions=None, locked_attempts=None, recording_close=False):
    # Session locks precede attempt/event locks throughout assessment writes.
    if not isinstance(document, dict) or not isinstance(document.get('events'), list):
        raise ProcessRequestError('invalid_request')
    sessions = locked_sessions if locked_sessions is not None else batch_session_contexts(
        student=student, event_documents=document['events'], locked=True)
    document = deepcopy(document)
    if isinstance(document.get('events'), list):
        document['events'].sort(key=lambda row: row.get('client_sequence_no', 0) if isinstance(row, dict) and isinstance(row.get('client_sequence_no'), int) else 0)
    return append_event_batch(
        student=student,
        document=document,
        request_received_at=request_received_at,
        locked_sessions=sessions,
        locked_attempts=locked_attempts,
        recording_close=recording_close,
    )


def uses_capture_v2(session):
    return bool(session.capture_contract_id and session.capture_contract.contract_name == 'capture-v2')


class ResponseVersionConflict(ValidationError):
    def __init__(self, attempt, candidate):
        self.document = {
            'code': 'response_version_conflict', 'expected_version': attempt.response_version,
            'current_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None,
            'current_answer': attempt.draft_answer,
            'candidate_event_id': str(candidate.pk),
            'candidate_answer': candidate.payload['after_state']['value'],
        }
        super().__init__('另一设备保存了不同答案，请选择继续使用的版本。')


def record_response_pointer_conflict(
    *, attempt, conflict, requested_expected_version, request_client_instance_id,
):
    """Append the server's observation of a rejected response-pointer write."""
    from .event_services import append_assessment_event

    requested_version = (
        requested_expected_version
        if type(requested_expected_version) is int
        and requested_expected_version >= 0
        else None
    )
    try:
        client_instance_id = (
            str(uuid.UUID(str(request_client_instance_id)))
            if request_client_instance_id is not None
            else None
        )
    except (TypeError, ValueError):
        client_instance_id = None
    document = conflict.document
    candidate_event_id = document['candidate_event_id']
    return append_assessment_event(
        event_type='response_pointer_conflict_observed',
        event_version=1,
        event_domain='assessment',
        actor_source='server',
        session=attempt.session,
        attempt=attempt,
        correlation_id=uuid.UUID(candidate_event_id),
        payload={
            'capture_version': 'capture-v2',
            'candidate_event_id': candidate_event_id,
            'current_event_id': document['current_event_id'],
            'requested_expected_version': requested_version,
            'current_response_version': document['expected_version'],
            'request_client_instance_id': client_instance_id,
        },
        capture_mode='native',
        app_version='server',
    )


def select_response_target(attempt, target_event_id, expected_version, branch_resolution=None):
    try:
        target = AssessmentEvent.objects.get(
            pk=target_event_id, attempt=attempt, event_type='response_transition',
        )
    except (AssessmentEvent.DoesNotExist, ValueError, ValidationError):
        raise ValidationError('目标答案状态尚未持久化，不能保存或提交。') from None
    if attempt.active_response_event_id == target.pk and branch_resolution is None:
        return target
    if expected_version != attempt.response_version:
        raise ResponseVersionConflict(attempt, target)
    if branch_resolution is not None:
        from .event_services import append_assessment_event
        candidates = branch_resolution['candidate_event_ids']
        if (len(candidates) != 2 or len(set(candidates)) != 2
                or str(attempt.active_response_event_id) not in candidates
                or str(target.pk) not in candidates
                or AssessmentEvent.objects.filter(pk__in=candidates, attempt=attempt, event_type='response_transition').count() != 2):
            raise ValidationError('冲突版本已变化，请重新核对。')
        append_assessment_event(event_type='response_branch_resolved', event_version=1,
            event_domain='assessment', actor_source='server', session=attempt.session,
            attempt=attempt, payload=branch_resolution, app_version='capture-v2',
            event_id=branch_resolution['operation_id'])
        return target
    # A stale device cannot silently make an alternate descendant active even
    # after learning the current version number; branch selection is explicit.
    current = target
    while current and current.pk != attempt.active_response_event_id:
        parent = current.payload.get('parent_event_id')
        current = AssessmentEvent.objects.filter(pk=parent, attempt=attempt).first() if parent else None
    if attempt.active_response_event_id and current is None:
        raise ResponseVersionConflict(attempt, target)
    return target


def resolve_response_branch(*, student, attempt_id, candidate_event_ids, selected_event_id,
                            expected_version, operation_id, writer_lease_token=None, writer_client_instance_id=None):
    from .models import Attempt
    from .services import _save_draft_transaction
    from .event_schema_registry import validate_event_payload
    if not Attempt.objects.filter(pk=attempt_id, session__student=student).exists():
        raise ValidationError('无权访问该题。')
    document = {'capture_version': 'capture-v2', 'operation_id': operation_id,
                'candidate_event_ids': candidate_event_ids, 'selected_event_id': selected_event_id,
                'expected_version': expected_version}
    validate_event_payload('response_branch_resolved', 1, document)
    attempt, time_error = _save_draft_transaction(
        attempt_id, None, target_event_id=selected_event_id, expected_version=expected_version,
        branch_resolution=document,
        writer_lease_token=writer_lease_token, writer_client_instance_id=writer_client_instance_id,
    )
    if time_error:
        raise ValidationError(time_error)
    return attempt
