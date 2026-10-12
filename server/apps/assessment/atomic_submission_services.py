"""One bounded commit for frozen process evidence and its formal response."""
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.analytics.research_materialization_jobs import coalesce_session_materializations

from .capture_v2_services import ResponseVersionConflict, ingest_capture_batch, uses_capture_v2
from .event_batch_services import ProcessRequestError, parse_uuid, require_exact_mapping
from .models import Attempt, ExperimentSession
from .recording_capture_services import record_client_capture_checkpoint
from .services import _validate_course_access, finish_session, save_draft, submit_attempt

MAX_EVENTS = 100
MAX_CHECKPOINTS = 100
MAX_CHECKPOINT_BYTES = 256 * 1024
MAX_BYTES = 8 * 1024 * 1024
BUNDLE_FIELDS = frozenset({'version', 'client_instance_id', 'event_boundary',
                           'events', 'checkpoints', 'expected_version'})
SUBMISSION_FIELDS = frozenset({'submission_token', 'target_event_id', 'confidence',
                               'client_clicked_at', 'writer_lease_token',
                               'writer_client_instance_id', 'atomic_submission'})


def atomic_submission_capability(attempt):
    course_id = attempt.session.version.experiment.course_id
    allowed = (
        '*' in settings.ATOMIC_SUBMISSION_USER_IDS
        or '*' in settings.ATOMIC_SUBMISSION_COURSE_IDS
        or str(attempt.session.student_id) in settings.ATOMIC_SUBMISSION_USER_IDS
        or str(course_id) in settings.ATOMIC_SUBMISSION_COURSE_IDS
    )
    return {
        'enabled': bool(settings.ATOMIC_SUBMISSION_ENABLED and allowed and uses_capture_v2(attempt.session)),
        'version': 1, 'max_events': MAX_EVENTS,
        'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES,
    }


def validate_process_bundle(attempt, document):
    """Validate the shared bounded evidence envelope without performing writes."""
    bundle = require_exact_mapping(document, allowed=BUNDLE_FIELDS)
    if type(bundle['version']) is not int or bundle['version'] != 1:
        raise ProcessRequestError('unsupported_atomic_submission_version')
    client_id = parse_uuid(bundle['client_instance_id'])
    for field, limit in (('events', MAX_EVENTS), ('checkpoints', MAX_CHECKPOINTS)):
        rows = bundle[field]
        if not isinstance(rows, list):
            raise ProcessRequestError('invalid_atomic_submission')
        if len(rows) > limit:
            raise ProcessRequestError('atomic_submission_too_large', status=413)
    for field in ('event_boundary', 'expected_version'):
        if type(bundle[field]) is not int or bundle[field] < 0:
            raise ProcessRequestError('invalid_atomic_submission')
    for event in bundle['events']:
        if (not isinstance(event, dict) or type(event.get('session_id')) is not int
                or event['session_id'] != attempt.session_id
                or event.get('attempt_id') != attempt.pk):
            raise ProcessRequestError('invalid_bundle_link')
        if type(event.get('client_sequence_no')) is not int or not 0 < event['client_sequence_no'] <= bundle['event_boundary']:
            raise ProcessRequestError('invalid_bundle_boundary')
    for checkpoint in bundle['checkpoints']:
        if (not isinstance(checkpoint, dict) or checkpoint.get('session_id') != attempt.session_id
                or parse_uuid(checkpoint.get('client_instance_id')) != client_id):
            raise ProcessRequestError('invalid_bundle_link')
        if type(checkpoint.get('highest_emitted_sequence_no')) is not int or checkpoint['highest_emitted_sequence_no'] > bundle['event_boundary']:
            raise ProcessRequestError('invalid_bundle_boundary')
        try:
            checkpoint_size = len(json.dumps(
                checkpoint, sort_keys=True, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False,
            ).encode('utf-8'))
        except (TypeError, ValueError) as error:
            raise ProcessRequestError('invalid_atomic_submission') from error
        if checkpoint_size > MAX_CHECKPOINT_BYTES:
            raise ProcessRequestError('checkpoint_too_large', status=413)
    return bundle


def process_material_fingerprint(material):
    """Hash bounded immutable material; leases are validated separately."""
    try:
        canonical = json.dumps(material, sort_keys=True, ensure_ascii=False,
                               separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise ProcessRequestError('invalid_atomic_submission') from error
    if len(canonical) > MAX_BYTES:
        raise ProcessRequestError('atomic_submission_too_large', status=413)
    return hashlib.sha256(canonical).hexdigest()


def _validate_bundle(attempt, document):
    require_exact_mapping(document, allowed=SUBMISSION_FIELDS,
                          required={'submission_token', 'target_event_id', 'atomic_submission'})
    bundle = validate_process_bundle(attempt, document.get('atomic_submission'))
    parse_uuid(document.get('target_event_id'))
    if document.get('client_clicked_at') is not None:
        try:
            client_time = parse_datetime(document['client_clicked_at'])
        except (TypeError, ValueError) as error:
            raise ProcessRequestError('invalid_client_clicked_at') from error
        if client_time is None or not timezone.is_aware(client_time):
            raise ProcessRequestError('invalid_client_clicked_at')
    if document.get('writer_client_instance_id') is not None and parse_uuid(document['writer_client_instance_id']) != parse_uuid(bundle['client_instance_id']):
        raise ProcessRequestError('invalid_bundle_link')
    material = {'atomic_submission': bundle, 'target_event_id': document.get('target_event_id'),
                'confidence': document.get('confidence')}
    return bundle, process_material_fingerprint(material)


def submit_attempt_bundle(*, student, attempt_id, document, request_received_at=None):
    """Return a receipt only after the outer transaction has really committed."""
    request_received_at = request_received_at or timezone.now()
    token = parse_uuid(document.get('submission_token'))
    with coalesce_session_materializations(), transaction.atomic(durable=True):
        session_id = Attempt.objects.filter(pk=attempt_id, session__student=student).values_list('session_id', flat=True).get()
        session = ExperimentSession.objects.select_for_update(of=('self',)).select_related(
            'version__experiment__course', 'capture_contract', 'class_task',
        ).get(pk=session_id, student=student)
        attempt = Attempt.objects.select_for_update(of=('self',)).get(pk=attempt_id, session=session)
        attempt.session = session
        bundle, fingerprint = _validate_bundle(attempt, document)
        if attempt.status == Attempt.Status.SUBMITTED:
            _validate_course_access(session, request_received_at=request_received_at)
            if attempt.submission_token != token or attempt.submission_fingerprint != fingerprint:
                raise ProcessRequestError('submission_material_conflict', status=409)
            receipt = attempt.submission_receipt
        else:
            if not atomic_submission_capability(attempt)['enabled']:
                raise ProcessRequestError('atomic_submission_disabled', status=409)
            acknowledgements = []
            if bundle['events']:
                acknowledgements = ingest_capture_batch(student=student, document={
                    'client_instance_id': bundle['client_instance_id'], 'events': bundle['events'],
                }, request_received_at=request_received_at)
            checkpoint_ids = [str(record_client_capture_checkpoint(
                student=student, document=checkpoint,
            ).pk) for checkpoint in bundle['checkpoints']]
            client_time = parse_datetime(document['client_clicked_at']) if document.get('client_clicked_at') else None
            try:
                save_draft(attempt.pk, None, client_time,
                           writer_lease_token=document.get('writer_lease_token'),
                           writer_client_instance_id=document.get('writer_client_instance_id'),
                           request_received_at=request_received_at,
                           target_event_id=document.get('target_event_id'),
                           expected_version=bundle['expected_version'])
            except ResponseVersionConflict as error:
                raise ProcessRequestError('response_version_conflict', status=409) from error
            attempt = submit_attempt(attempt.pk, token, client_time,
                                     writer_lease_token=document.get('writer_lease_token'),
                                     writer_client_instance_id=document.get('writer_client_instance_id'),
                                     request_received_at=request_received_at,
                                     target_event_id=document.get('target_event_id'),
                                     confidence_response=document.get('confidence'))
            receipt = {
                'committed': True, 'acknowledgements': acknowledgements,
                'checkpoint_ids': checkpoint_ids,
                'final_response_event_id': str(attempt.final_response_event_id),
                'response_version': attempt.response_version,
            }
            attempt.submission_fingerprint = fingerprint
            attempt.submission_receipt = receipt
            attempt.save(update_fields=['submission_fingerprint', 'submission_receipt'])
            remaining = session.assignments.exclude(pk=attempt.assignment_id).exclude(
                attempt__status__in=(Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED),
            ).exists()
            if not remaining:
                attempt.session = finish_session(session.pk)
    return attempt, receipt
