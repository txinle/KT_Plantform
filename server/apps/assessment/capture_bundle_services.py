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
from .models import AssessmentEvent, Attempt, CaptureMutationReceipt, ExperimentSession
from .recording_capture_services import capture_checkpoint_context, record_client_capture_checkpoint, validate_close_recording_time
from .services import _validate_course_access, _validate_process_event_time, save_draft


def capture_bundle_capability(session):
    return {'enabled': uses_capture_v2(session) and (settings.CAPTURE_BUNDLE_ENABLED or session.assessment_flow_version == 1),
            'version': 1, 'recovery_supported': True, 'mutation_receipts': True, 'max_events': MAX_EVENTS,
            'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES}


def close_bundle_capability(session):
    return {'enabled': bool(uses_capture_v2(session) and (session.assessment_flow_version == 1 or
                settings.CAPTURE_BUNDLE_ENABLED and getattr(settings, 'CLOSE_BUNDLE_ENABLED', False))),
            'version': 1, 'max_events': MAX_EVENTS, 'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES}


def _locked_session(student, session_id):
    # Serialize mutations while allowing independent time-sync foreign-key inserts.
    session = ExperimentSession.objects.select_for_update(of=('self',), no_key=True).select_related(
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
    from .writer_lease_services import _bind_locked_attempt
    _bind_locked_attempt(attempt)
    return attempt


def _ingest(student, bundle, session, request_received_at, attempt=None, *, recording_close=False, validation_context=None):
    bundle = validate_process_bundle(attempt, bundle, session=session, capture_scope=True)
    # Also bound non-HTTP service callers; never accept material that JSON cannot preserve.
    process_material_fingerprint(bundle)
    try:
        time_error = (validate_close_recording_time(session, attempt, request_received_at=request_received_at)
            if recording_close else _validate_process_event_time(session, attempt, request_received_at=request_received_at,
                validation_context=validation_context))
    except ValidationError as error:
        raise ProcessConflictError('course_access_revoked') from error
    if time_error:
        raise ProcessConflictError('task_terminated' if time_error == '班级任务已结束' else 'deadline_expired')
    acknowledgements = ingest_capture_batch(student=student, document={
        'client_instance_id': bundle['client_instance_id'], 'events': bundle['events'],
    }, request_received_at=request_received_at, locked_sessions={session.pk: session},
        locked_attempts={attempt.pk: attempt} if attempt is not None else None,
        recording_close=recording_close,
        validation_contexts={session.pk: validation_context} if validation_context is not None else None) if bundle['events'] else []
    try:
        context = capture_checkpoint_context(student=student, bundle=bundle, session=session)
        checkpoint_ids = [str(record_client_capture_checkpoint(student=student, document=row, context=context).pk)
                          for row in bundle['checkpoints']]
    except ValidationError as error:
        raise ProcessRequestError('invalid_checkpoint', status=409) from error
    return {'acknowledgements': acknowledgements, 'checkpoint_ids': checkpoint_ids}


def close_attempt_capture_bundle(*, student, attempt_id, document, request_received_at=None):
    from .writer_lease_services import WriterLeaseConflict, release_attempt_writer_lease
    require_exact_mapping(document, allowed={'lease_token', 'client_instance_id', 'capture_bundle',
        'capture_operation_id', 'leave_event_id', 'next_entry'},
        required={'lease_token', 'client_instance_id', 'capture_bundle', 'capture_operation_id', 'leave_event_id'})
    process_material_fingerprint(document)
    token = parse_uuid(document['capture_operation_id'])
    leave_id = parse_uuid(document['leave_event_id'])
    client = parse_uuid(document['client_instance_id'])
    lease_token = parse_uuid(document['lease_token'])
    fingerprint = process_material_fingerprint({key: value for key, value in document.items()
        if key not in {'lease_token', 'client_instance_id'}})
    request_received_at = request_received_at or timezone.now()
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        attempt = _locked_attempt(student, attempt_id)
        from .transition_entry_services import validate_next_entry
        validate_next_entry(attempt, document)
        receipt = _existing_mutation_receipt(student, attempt, token, 'close', fingerprint, request_received_at)
        if receipt is None:
            if not close_bundle_capability(attempt.session)['enabled']:
                raise ProcessConflictError('close_bundle_disabled')
            bundle = validate_process_bundle(attempt, document['capture_bundle'], capture_scope=True)
            if parse_uuid(bundle['client_instance_id']) != client:
                raise ProcessRequestError('invalid_bundle_link')
            # An unseen close must release its own lease, including when it expired.
            if attempt.writer_lease_token != lease_token or attempt.writer_client_instance_id != client:
                raise WriterLeaseConflict('writer lease is not owned by client')
            evidence = _ingest(student, bundle, attempt.session, request_received_at, attempt, recording_close=True)
            left = AssessmentEvent.objects.filter(pk=leave_id, session=attempt.session, attempt=attempt,
                event_type='item_left', client_instance_id=client).first()
            if left is None or left.client_sequence_no > bundle['event_boundary']:
                raise ProcessRequestError('invalid_close_leave_event')
            release_attempt_writer_lease(attempt, lease_token=lease_token, client_instance_id=client,
                now=request_received_at, _locked_attempt=attempt)
            receipt = {'success': True, 'committed': True, 'capture_operation_id': str(token), **evidence,
                'writer_lease_released': True, 'status': attempt.status, 'response_version': attempt.response_version,
                'active_response_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None,
                'final_response_event_id': str(attempt.final_response_event_id) if attempt.final_response_event_id else None}
            CaptureMutationReceipt.objects.create(attempt=attempt, token=token, kind=CaptureMutationReceipt.Kind.CLOSE,
                material_fingerprint=fingerprint, receipt=receipt)
    return receipt


def ingest_capture_bundle(*, student, document, request_received_at=None):
    require_exact_mapping(document, allowed={'capture_bundle'})
    bundle = document['capture_bundle']
    if not isinstance(bundle, dict):
        raise ProcessRequestError('invalid_request')
    process_material_fingerprint(document)
    session_ids = set()
    for name, limit in (('events', MAX_EVENTS), ('checkpoints', MAX_CHECKPOINTS)):
        rows = bundle.get(name)
        if not isinstance(rows, list):
            raise ProcessRequestError('invalid_request')
        if len(rows) > limit:
            raise ProcessRequestError('atomic_submission_too_large', status=413)
        for row in rows:
            if not isinstance(row, dict) or type(row.get('session_id')) is not int:
                raise ProcessRequestError('invalid_bundle_link')
            session_ids.add(row['session_id'])
    if not session_ids:
        raise ProcessRequestError('invalid_bundle_link')
    request_received_at = request_received_at or timezone.now()
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        # One browser outbox may retain facts from several owned sessions.
        # Lock in one order and confirm the original packet only after all commit.
        sessions = [_locked_session(student, session_id) for session_id in sorted(session_ids)]
        from apps.accounts.research_consent import research_consent_required
        if research_consent_required(student):
            raise ProcessRequestError('research_consent_required', status=403)
        receipt = {'acknowledgements': [], 'checkpoint_ids': []}
        for session in sessions:
            part = {**bundle, 'events': [row for row in bundle['events'] if row['session_id'] == session.pk],
                'checkpoints': [row for row in bundle['checkpoints'] if row['session_id'] == session.pk]}
            confirmed = _ingest(student, part, session, request_received_at,
                recording_close=session.status != ExperimentSession.Status.IN_PROGRESS)
            receipt['acknowledgements'].extend(confirmed['acknowledgements'])
            receipt['checkpoint_ids'].extend(confirmed['checkpoint_ids'])
    return {**receipt, 'committed': True}


def save_draft_capture_bundle(*, student, attempt_id, document, request_received_at=None):
    from .writer_lease_services import WriterLeaseConflict
    require_exact_mapping(document, allowed={'answer', 'client_occurred_at', 'target_event_id',
        'expected_version', 'writer_lease_token', 'writer_client_instance_id', 'capture_bundle', 'capture_operation_id', 'inline_confidence'},
        required={'capture_bundle', 'capture_operation_id'})
    process_material_fingerprint(document)
    token = parse_uuid(document['capture_operation_id'])
    fingerprint = _mutation_fingerprint(document)
    request_received_at = request_received_at or timezone.now()
    rejection = None
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        attempt = _locked_attempt(student, attempt_id)
        from .services import mutation_validation_context
        try:
            validation_context = mutation_validation_context(attempt.session, request_received_at)
        except ValidationError as error:
            raise ProcessConflictError('course_access_revoked') from error
        receipt = _existing_mutation_receipt(student, attempt, token, 'draft', fingerprint, request_received_at,
            validation_context=validation_context)
        if receipt is None:
            try:
                # Only a completed rollback of this savepoint proves rejection.
                # The outer commit must still succeed before exposing that proof.
                with transaction.atomic():
                    evidence = _ingest(student, document['capture_bundle'], attempt.session, request_received_at, attempt,
                        validation_context=validation_context)
                    attempt = save_draft(attempt_id, document.get('answer'),
                        parse_datetime(document['client_occurred_at']) if document.get('client_occurred_at') else None,
                        request_received_at=request_received_at, target_event_id=document.get('target_event_id'),
                        expected_version=document.get('expected_version'), writer_lease_token=document.get('writer_lease_token'),
                        writer_client_instance_id=document.get('writer_client_instance_id'), _locked_attempt=attempt,
                        inline_confidence=document.get('inline_confidence'), _validation_context=validation_context)
                    receipt = {**draft_response(attempt), **evidence, 'committed': True,
                        'receipt_version': 2, 'capture_operation_id': str(token), 'attempt_id': attempt.pk,
                        'material_fingerprint': fingerprint}
            except (ValidationError, WriterLeaseConflict, ProcessRequestError) as error:
                rejection = error
                receipt = mutation_rejection_receipt(error, attempt, token, fingerprint, kind='draft')
            CaptureMutationReceipt.objects.create(attempt=attempt, token=token, kind='draft',
                material_fingerprint=fingerprint, receipt=receipt)
    if rejection is not None:
        attach_mutation_rejection(rejection, receipt)
        raise rejection
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


def _existing_mutation_receipt(student, attempt, token, kind, fingerprint, request_received_at, *, validation_context=None):
    from apps.accounts.research_consent import research_consent_required
    if research_consent_required(student):
        raise ProcessRequestError('research_consent_required', status=403)
    try:
        _validate_course_access(attempt.session, request_received_at=request_received_at, validation_context=validation_context)
    except ValidationError as error:
        raise ProcessConflictError('course_access_revoked') from error
    existing = CaptureMutationReceipt.objects.filter(attempt=attempt, token=token).first()
    if existing is None:
        return None
    if existing.kind != kind or existing.material_fingerprint != fingerprint:
        raise ProcessConflictError('capture_mutation_material_conflict')
    if existing.receipt.get('outcome') == 'rejected':
        raise_mutation_rejection(existing.receipt)
    return existing.receipt


def mutation_rejection_receipt(error, attempt, token, fingerprint, *, kind):
    """Freeze only domain rejections after their writes have rolled back."""
    from .writer_lease_services import WriterLeaseConflict
    fields = dict(error.document) if isinstance(error, ResponseVersionConflict) else {}
    if isinstance(error, ProcessRequestError):
        code, status = error.code, error.status
        message = getattr(error, 'message', error.code)
    elif isinstance(error, WriterLeaseConflict):
        code, status = 'writer_lease_conflict', 409
        message = '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'
    else:
        code, status = ('RESPONSE_VERSION_CONFLICT' if isinstance(error, ResponseVersionConflict)
            else 'navigation_rejected' if kind == 'navigation' else 'draft_rejected'), 409
        message = '; '.join(error.messages)
    fields.update(mutation_rejected=True, receipt_version=2, mutation_kind=kind,
        attempt_id=attempt.pk, material_fingerprint=fingerprint)
    if kind == 'navigation':
        fields.update(navigation_rejected=True, navigation_token=str(token))
    else:
        fields['capture_operation_id'] = str(token)
    return {'receipt_version': 2, 'outcome': 'rejected', 'http_status': status,
        'error': {'code': code, 'message': message, 'fields': fields}}


def attach_mutation_rejection(error, receipt):
    error.mutation_rejection_receipt = receipt
    if receipt['error']['fields']['mutation_kind'] == 'navigation':
        error.navigation_rejection_token = receipt['error']['fields']['navigation_token']


def raise_mutation_rejection(receipt):
    error = ProcessRequestError(receipt['error']['code'], status=receipt['http_status'])
    attach_mutation_rejection(error, receipt)
    raise error


def draft_response(attempt):
    """The existing draft projection, also frozen in the durable mutation receipt."""
    response = {'success': True, 'saved_at': attempt.draft_saved_at.isoformat(),
        'response_version': attempt.response_version,
        'active_response_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None}
    if hasattr(attempt, '_inline_confidence_receipt'):
        response['inline_confidence'] = attempt._inline_confidence_receipt
    snapshot = attempt.saved_response_snapshot
    if snapshot is not None:
        response.update(response_snapshot_id=str(snapshot.pk), content_hash=snapshot.content_hash,
            content_length=snapshot.content_length, state_hash=snapshot.state_hash)
    elif attempt.saved_response_state is not None:
        response['response_state_after'] = attempt.saved_response_state
    elif attempt.assignment.snapshot.question_type in {'single_choice', 'multiple_choice', 'true_false'}:
        response['response_state_after'] = None
    return response
