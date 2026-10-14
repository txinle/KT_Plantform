"""One bounded durable write for ordinary draft-preserving navigation."""
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.exceptions import ValidationError

from .atomic_submission_services import MAX_BYTES, MAX_CHECKPOINTS, MAX_EVENTS, process_material_fingerprint, validate_process_bundle
from .capture_v2_services import ingest_capture_batch, uses_capture_v2
from .event_batch_services import ProcessRequestError, parse_uuid, require_exact_mapping
from .models import AssessmentEvent, Attempt, ExperimentSession, NavigationReceipt
from .recording_capture_services import record_client_capture_checkpoint
from .services import _validate_course_access, _validate_process_event_time, save_draft
from .writer_lease_services import WriterLeaseConflict, acquire_attempt_writer_lease, assert_attempt_writer_lease, release_attempt_writer_lease

FIELDS = frozenset({'navigation_token', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at',
                    'writer_lease_token', 'writer_client_instance_id', 'save_and_leave', 'next_entry'})


def saved_navigation_capability(attempt):
    allowed = ('*' in settings.SAVE_AND_LEAVE_USER_IDS or '*' in settings.SAVE_AND_LEAVE_COURSE_IDS
               or str(attempt.session.student_id) in settings.SAVE_AND_LEAVE_USER_IDS
               or str(attempt.session.version.experiment.course_id) in settings.SAVE_AND_LEAVE_COURSE_IDS)
    return {'enabled': bool(settings.SAVE_AND_LEAVE_ENABLED and allowed and uses_capture_v2(attempt.session)),
            'version': 1, 'max_events': MAX_EVENTS, 'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES}


def save_and_leave_attempt(*, student, attempt_id, document, request_received_at=None):
    outcome = {'new_operation': False, 'body_completed': False}
    try:
        return _save_and_leave_attempt(student=student, attempt_id=attempt_id, document=document,
                                      request_received_at=request_received_at, outcome=outcome)
    except (ValidationError, WriterLeaseConflict, ProcessRequestError) as error:
        # This marker is only set after the durable transaction has rolled back.
        # A failed COMMIT or an existing receipt conflict leaves the outcome unknown.
        if outcome['new_operation'] and not outcome['body_completed']:
            error.navigation_rejection_token = str(document['navigation_token'])
        raise


def _save_and_leave_attempt(*, student, attempt_id, document, request_received_at, outcome):
    require_exact_mapping(document, allowed=FIELDS,
                          required={'navigation_token', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'save_and_leave'})
    token = parse_uuid(document['navigation_token'])
    leave_id = parse_uuid(document['leave_event_id'])
    destination_id = document['to_assignment_id']
    if type(destination_id) is not int or destination_id <= 0:
        raise ProcessRequestError('invalid_navigation_destination')
    clicked_at = document.get('client_clicked_at')
    try:
        client_time = parse_datetime(clicked_at) if clicked_at is not None else None
    except (TypeError, ValueError) as error:
        raise ProcessRequestError('invalid_client_clicked_at') from error
    if clicked_at is not None and (client_time is None or not timezone.is_aware(client_time)):
        raise ProcessRequestError('invalid_client_clicked_at')
    request_received_at = request_received_at or timezone.now()
    with transaction.atomic(durable=True):
        session_id = Attempt.objects.filter(pk=attempt_id, session__student=student).values_list('session_id', flat=True).get()
        session = ExperimentSession.objects.select_for_update(of=('self',)).select_related(
            'version__experiment__course', 'capture_contract', 'class_task',
        ).get(pk=session_id, student=student)
        attempt = Attempt.objects.select_for_update(of=('self',)).get(pk=attempt_id, session=session)
        attempt.session = session
        from .transition_entry_services import validate_next_entry
        validate_next_entry(attempt, document, destination_id=destination_id)
        bundle = validate_process_bundle(attempt, document['save_and_leave'])
        client_id = parse_uuid(bundle['client_instance_id'])
        target = document['target_event_id']
        if target is not None:
            parse_uuid(target)
        if document.get('writer_client_instance_id') is not None and parse_uuid(document['writer_client_instance_id']) != client_id:
            raise ProcessRequestError('invalid_bundle_link')
        material = {k: document.get(k) for k in
            ['save_and_leave', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at']}
        if 'next_entry' in document:
            material['next_entry'] = document['next_entry']
        fingerprint = process_material_fingerprint(material)
        _validate_course_access(session, request_received_at=request_received_at)
        existing = NavigationReceipt.objects.filter(attempt=attempt, token=token).first()
        if existing is not None:
            if existing.material_fingerprint != fingerprint:
                raise ProcessRequestError('navigation_material_conflict', status=409)
            receipt = existing.receipt
        else:
            outcome['new_operation'] = True
            if not saved_navigation_capability(attempt)['enabled']:
                raise ProcessRequestError('save_and_leave_disabled', status=409)
            if destination_id == attempt.assignment_id or not session.assignments.filter(pk=destination_id).exists():
                raise ProcessRequestError('invalid_navigation_destination')
            time_error = _validate_process_event_time(session, request_received_at=request_received_at)
            if time_error:
                raise ValidationError(time_error)
            if session.status != ExperimentSession.Status.IN_PROGRESS:
                raise ValidationError('当前会话不可继续作答')
            if document.get('writer_lease_token') is not None:
                assert_attempt_writer_lease(attempt, lease_token=document['writer_lease_token'],
                                           client_instance_id=client_id)
                lease_token = document['writer_lease_token']
            else:
                lease_token = acquire_attempt_writer_lease(attempt, client_instance_id=client_id).token
            acknowledgements = ingest_capture_batch(student=student, document={
                'client_instance_id': bundle['client_instance_id'], 'events': bundle['events'],
            }, request_received_at=request_received_at) if bundle['events'] else []
            left = AssessmentEvent.objects.filter(pk=leave_id, attempt=attempt, event_type='item_left',
                                                  client_instance_id=client_id, correlation_id=token).first()
            if (left is None or left.client_sequence_no > bundle['event_boundary']
                    or left.payload != {'leave_reason': 'navigation', 'to_assignment_id': destination_id}):
                raise ProcessRequestError('invalid_navigation_leave_event')
            try:
                checkpoint_ids = [str(record_client_capture_checkpoint(student=student, document=row).pk)
                                  for row in bundle['checkpoints']]
            except ValidationError as error:
                raise ProcessRequestError('invalid_capture_checkpoint') from error
            terminal = attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}
            if (target is None or terminal) and attempt.response_version != bundle['expected_version']:
                raise ProcessRequestError('navigation_response_version_conflict', status=409)
            if not terminal:
                if target is not None:
                    attempt = save_draft(attempt.pk, None, client_time, target_event_id=target,
                        expected_version=bundle['expected_version'], writer_lease_token=lease_token,
                        writer_client_instance_id=client_id, request_received_at=request_received_at)
                elif attempt.active_response_event_id is not None:
                    raise ProcessRequestError('missing_navigation_response_target')
            elif (str(attempt.active_response_event_id) if attempt.active_response_event_id else None) != target:
                raise ProcessRequestError('navigation_response_conflict', status=409)
            release_attempt_writer_lease(attempt, lease_token=lease_token, client_instance_id=client_id)
            attempt.refresh_from_db()
            receipt = {'success': True, 'committed': True, 'navigation_token': str(token),
                       'acknowledgements': acknowledgements, 'checkpoint_ids': checkpoint_ids,
                       'response_version': attempt.response_version,
                       'active_response_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None,
                       'saved_at': attempt.draft_saved_at.isoformat() if attempt.draft_saved_at else None,
                       'status': attempt.status, 'writer_lease_released': True, 'to_assignment_id': destination_id,
                       'next_url': reverse('assessment:question', args=[session.pk, destination_id])}
            NavigationReceipt.objects.create(attempt=attempt, token=token, material_fingerprint=fingerprint, receipt=receipt)
        outcome['body_completed'] = True
    return attempt, receipt
