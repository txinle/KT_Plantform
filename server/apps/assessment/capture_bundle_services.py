"""Commit bounded original capture facts with the existing domain operation."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.analytics.research_materialization_jobs import coalesce_session_materializations

from .atomic_submission_services import MAX_BYTES, MAX_CHECKPOINTS, MAX_EVENTS, process_material_fingerprint, validate_process_bundle
from .capture_v2_services import ResponseVersionConflict, ingest_capture_batch, uses_capture_v2
from .event_batch_services import ProcessConflictError, ProcessRequestError, parse_uuid, require_exact_mapping
from .models import Attempt, CaptureMutationReceipt, ClientCaptureCheckpoint, ClientTimeSync, ExperimentSession
from .recording_capture_services import record_client_capture_checkpoint
from .services import _validate_course_access, _validate_process_event_time, save_draft


def capture_bundle_capability(session):
    return {'enabled': uses_capture_v2(session) and settings.CAPTURE_BUNDLE_ENABLED,
            'version': 1, 'recovery_supported': True, 'mutation_receipts': True, 'max_events': MAX_EVENTS,
            'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES}


def _locked_session(student, session_id):
    session = ExperimentSession.objects.select_for_update(of=('self',)).select_related(
        'version__experiment__course', 'capture_contract', 'class_task',
    ).filter(pk=session_id, student=student).first()
    if session is None:
        raise ProcessRequestError('invalid_bundle_link')
    if not uses_capture_v2(session):
        raise ProcessRequestError('capture_contract_mismatch')
    return session


def _locked_attempt(student, attempt_id):
    session_id = Attempt.objects.filter(pk=attempt_id, session__student=student).values_list('session_id', flat=True).first()
    if session_id is None:
        raise ProcessRequestError('invalid_bundle_link')
    session = _locked_session(student, session_id)
    attempt = Attempt.objects.select_for_update(of=('self',)).select_related(
        'assignment__snapshot', 'assignment__item_instance', 'current_response_snapshot',
    ).get(pk=attempt_id, session=session)
    attempt.session = session
    return attempt


def _ingest(student, bundle, session, request_received_at, attempt=None):
    bundle = validate_process_bundle(attempt, bundle, session=session, capture_scope=True)
    # Also bound non-HTTP service callers; never accept material that JSON cannot preserve.
    process_material_fingerprint(bundle)
    try:
        time_error = _validate_process_event_time(session, attempt, request_received_at=request_received_at)
    except ValidationError as error:
        raise ProcessConflictError('course_access_revoked') from error
    if time_error:
        raise ProcessConflictError('task_terminated' if time_error == '班级任务已结束' else 'deadline_expired')
    acknowledgements = ingest_capture_batch(student=student, document={
        'client_instance_id': bundle['client_instance_id'], 'events': bundle['events'],
    }, request_received_at=request_received_at, locked_sessions={session.pk: session},
        locked_attempts={attempt.pk: attempt} if attempt is not None else None) if bundle['events'] else []
    try:
        rows = bundle['checkpoints']
        context = {
            'sessions': {session.pk: session},
            'time_syncs': {sample.pk: sample for sample in ClientTimeSync.objects.filter(
                pk__in={parse_uuid(row['time_sync_id']) for row in rows if row.get('time_sync_id') is not None},
                session=session, client_instance_id=parse_uuid(bundle['client_instance_id']))},
            'checkpoints': {row.pk: row for row in ClientCaptureCheckpoint.objects.filter(
                pk__in={parse_uuid(row.get('checkpoint_id')) for row in rows})},
        }
        checkpoint_ids = [str(record_client_capture_checkpoint(student=student, document=row, context=context).pk)
                          for row in bundle['checkpoints']]
    except ValidationError as error:
        raise ProcessRequestError('invalid_checkpoint', status=409) from error
    return {'acknowledgements': acknowledgements, 'checkpoint_ids': checkpoint_ids}


def ingest_capture_bundle(*, student, document, request_received_at=None):
    require_exact_mapping(document, allowed={'capture_bundle'})
    bundle = document['capture_bundle']
    if not isinstance(bundle, dict):
        raise ProcessRequestError('invalid_request')
    process_material_fingerprint(document)
    session_ids = set()
    for name in ('events', 'checkpoints'):
        rows = bundle.get(name)
        if not isinstance(rows, list):
            raise ProcessRequestError('invalid_request')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('session_id')) is not int:
                raise ProcessRequestError('invalid_bundle_link')
            session_ids.add(row['session_id'])
    if len(session_ids) != 1:
        raise ProcessRequestError('invalid_bundle_link')
    request_received_at = request_received_at or timezone.now()
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        session = _locked_session(student, session_ids.pop())
        receipt = _ingest(student, bundle, session, request_received_at)
    return {**receipt, 'committed': True}


def save_draft_capture_bundle(*, student, attempt_id, document, request_received_at=None):
    require_exact_mapping(document, allowed={'answer', 'client_occurred_at', 'target_event_id',
        'expected_version', 'writer_lease_token', 'writer_client_instance_id', 'capture_bundle', 'capture_operation_id'},
        required={'capture_bundle', 'capture_operation_id'})
    process_material_fingerprint(document)
    token = parse_uuid(document['capture_operation_id'])
    fingerprint = _mutation_fingerprint(document)
    request_received_at = request_received_at or timezone.now()
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        attempt = _locked_attempt(student, attempt_id)
        receipt = _existing_mutation_receipt(student, attempt, token, 'draft', fingerprint, request_received_at)
        if receipt is None:
            evidence = _ingest(student, document['capture_bundle'], attempt.session, request_received_at, attempt)
            try:
                attempt = save_draft(attempt_id, document.get('answer'),
                    parse_datetime(document['client_occurred_at']) if document.get('client_occurred_at') else None,
                    request_received_at=request_received_at, target_event_id=document.get('target_event_id'),
                    expected_version=document.get('expected_version'), writer_lease_token=document.get('writer_lease_token'),
                    writer_client_instance_id=document.get('writer_client_instance_id'), _locked_attempt=attempt)
            except ResponseVersionConflict as error:
                raise ProcessRequestError('response_version_conflict', status=409) from error
            receipt = {**draft_response(attempt), **evidence, 'committed': True}
            CaptureMutationReceipt.objects.create(attempt=attempt, token=token, kind='draft',
                material_fingerprint=fingerprint, receipt=receipt)
    return attempt, receipt


def record_tool_capture_bundle(*, student, attempt_id, document, request_received_at=None):
    from .research_tool_services import record_tool_action
    require_exact_mapping(document, allowed={'tool', 'stage', 'operation_id', 'data', 'answer_event_id',
        'initiation_type', 'capture_bundle'}, required={'tool', 'stage', 'operation_id', 'capture_bundle'})
    process_material_fingerprint(document)
    token = parse_uuid(document['operation_id'])
    fingerprint = _mutation_fingerprint(document)
    request_received_at = request_received_at or timezone.now()
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        attempt = _locked_attempt(student, attempt_id)
        receipt = _existing_mutation_receipt(student, attempt, token, 'tool', fingerprint, request_received_at)
        if receipt is None:
            evidence = _ingest(student, document['capture_bundle'], attempt.session, request_received_at, attempt)
            result = record_tool_action(student=student, attempt_id=attempt_id,
                request_received_at=request_received_at, _locked_attempt=attempt,
                **{key: value for key, value in document.items() if key != 'capture_bundle'})
            receipt = {'success': True, **result, **evidence, 'committed': True}
            CaptureMutationReceipt.objects.create(attempt=attempt, token=token, kind='tool',
                material_fingerprint=fingerprint, receipt=receipt)
    return receipt


def _mutation_fingerprint(document):
    return process_material_fingerprint({key: value for key, value in document.items()
        if key not in {'writer_lease_token', 'writer_client_instance_id'}})


def _existing_mutation_receipt(student, attempt, token, kind, fingerprint, request_received_at):
    from apps.accounts.research_consent import research_consent_required
    if research_consent_required(student):
        raise ProcessRequestError('research_consent_required', status=403)
    try:
        _validate_course_access(attempt.session, request_received_at=request_received_at)
    except ValidationError as error:
        raise ProcessConflictError('course_access_revoked') from error
    existing = CaptureMutationReceipt.objects.filter(attempt=attempt, token=token).first()
    if existing is None:
        return None
    if existing.kind != kind or existing.material_fingerprint != fingerprint:
        raise ProcessConflictError('capture_mutation_material_conflict')
    return existing.receipt


def draft_response(attempt):
    """The existing draft projection, also frozen in the durable mutation receipt."""
    response = {'success': True, 'saved_at': attempt.draft_saved_at.isoformat(),
        'response_version': attempt.response_version,
        'active_response_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None}
    snapshot = attempt.saved_response_snapshot
    if snapshot is not None:
        response.update(response_snapshot_id=str(snapshot.pk), content_hash=snapshot.content_hash,
            content_length=snapshot.content_length, state_hash=snapshot.state_hash)
    elif attempt.saved_response_state is not None:
        response['response_state_after'] = attempt.saved_response_state
    elif attempt.assignment.snapshot.question_type in {'single_choice', 'multiple_choice', 'true_false'}:
        response['response_state_after'] = None
    return response
