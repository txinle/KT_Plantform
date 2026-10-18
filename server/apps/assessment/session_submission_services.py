"""Accept saved final pointers quickly, then commit the frozen paper durably."""
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Min, Prefetch, Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from config.capture_timing import pending_timing
from apps.analytics.research_materialization_jobs import coalesce_session_materializations
from apps.experiments.models import QuestionSnapshot
from apps.question_bank.services.scoring import score_payload
from apps.question_bank.services.numeric import normalize_finite_decimal
from .deadline_services import DeadlineDecisionService
from .event_batch_services import ProcessRequestError, parse_uuid, require_exact_mapping
from .models import Attempt, ConfidenceAssignment, ExperimentSession, ResponseSnapshot
from .event_services import session_event_sequence_context
from .session_submission_models import SessionSubmission
from .services import (_append_attempt_closed, _append_session_event, _commit_attempt_submission,
                       _schedule_research_materialization, _validate_course_access)

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger('kt.session_submission_timing')
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
WORKER_APPLICATION_NAME = 'kt-session-submission-worker'


def _timed_submission_phase(phase):
    def decorate(function):
        @wraps(function)
        def timed(*args, **kwargs):
            state = pending_timing.get()
            if state is None and not (getattr(settings, 'ASSESSMENT_REQUEST_TIMING_ENABLED', False)
                                      or getattr(settings, 'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED', False)):
                return function(*args, **kwargs)
            started, started_at = time.perf_counter(), time.time()
            result, error_type = None, None
            try:
                result = function(*args, **kwargs)
                return result
            except Exception as error:
                error_type = type(error).__name__
                raise
            finally:
                seconds = round(time.perf_counter() - started, 6)
                if state is not None:
                    state.setdefault('capture_metrics', {})[phase + '_seconds'] = seconds
                # Empty worker polls do not produce a per-submission timing row.
                if result is not None or error_type:
                    row = {'phase': phase, 'started_at': started_at, 'ended_at': time.time(),
                           'seconds': seconds, 'error_type': error_type}
                    if isinstance(result, SessionSubmission):
                        row.update(submission_id=str(result.pk), status=result.status,
                                   accepted_at=result.accepted_at.isoformat())
                    timing_logger.warning(json.dumps(row, separators=(',', ':')))
        return timed
    return decorate


@_timed_submission_phase('submission_material')
def _request_material(document):
    require_exact_mapping(document, allowed={'submission_token', 'client_clicked_at', 'expected_versions'},
                          required={'submission_token', 'expected_versions'})
    token = parse_uuid(document['submission_token'])
    clicked_at = None
    if document.get('client_clicked_at') is not None:
        try:
            clicked_at = parse_datetime(document['client_clicked_at'])
        except (TypeError, ValueError):
            raise ProcessRequestError('invalid_client_clicked_at') from None
        if clicked_at is None or not timezone.is_aware(clicked_at):
            raise ProcessRequestError('invalid_client_clicked_at')
    supplied = document['expected_versions']
    if not isinstance(supplied, list):
        raise ProcessRequestError('invalid_submission_manifest')
    rows = []
    for row in supplied:
        require_exact_mapping(row, allowed={'attempt_id', 'response_version', 'target_event_id'})
        if type(row['attempt_id']) is not int or row['attempt_id'] < 1:
            raise ProcessRequestError('invalid_submission_manifest')
        if type(row['response_version']) is not int or row['response_version'] < 0:
            raise ProcessRequestError('invalid_submission_manifest')
        event_id = str(parse_uuid(row['target_event_id'])) if row['target_event_id'] is not None else None
        rows.append({'attempt_id': row['attempt_id'], 'response_version': row['response_version'],
                     'target_event_id': event_id})
    rows.sort(key=lambda row: row['attempt_id'])
    if len({row['attempt_id'] for row in rows}) != len(rows):
        raise ProcessRequestError('invalid_submission_manifest')
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return token, clicked_at, rows, fingerprint


@_timed_submission_phase('submission_accept')
def accept_session_submission(*, student, session_id, document, request_received_at=None):
    token, clicked_at, rows, fingerprint = _request_material(document)
    received_at = request_received_at or timezone.now()
    try:
        with transaction.atomic(durable=True):
            try:
                session = ExperimentSession.objects.select_for_update(no_key=True, of=('self',)).select_related(
                    'version__experiment__course', 'class_task', 'participant_snapshot',
                ).get(pk=session_id, student=student)
            except ExperimentSession.DoesNotExist:
                raise Http404 from None
            existing = SessionSubmission.objects.filter(session=session).first()
            if existing is not None:
                if existing.material_fingerprint != fingerprint:
                    raise ProcessRequestError('submission_material_conflict', status=409)
                return existing
            if session.assessment_flow_version != 1 or session.status != ExperimentSession.Status.IN_PROGRESS:
                raise ProcessRequestError('session_not_submittable', status=409)
            _validate_course_access(session, request_received_at=received_at)
            if session.class_task_id:
                participant = session.participant_snapshot
                if (participant is None or participant.student_id != student.pk
                        or participant.class_task_id != session.class_task_id):
                    raise Http404
                decision = DeadlineDecisionService.can_submit(session.class_task, session, None, received_at)
                if not decision.allowed:
                    raise ProcessRequestError(decision.code, status=409)
            elif session.deadline_at is not None and received_at >= session.deadline_at:
                raise ProcessRequestError('DEADLINE_EXPIRED', status=409)
            required_tools = any(rule.get('required') for rule in
                                 session.version.configuration.get('research_tools', {}).get('rules', []))
            attempts_query = session.attempts.select_related('current_response_snapshot')
            if required_tools:
                attempts_query = attempts_query.select_related('assignment').prefetch_related(
                    Prefetch('assignment__snapshot', queryset=QuestionSnapshot.objects.only('pk', 'code')))
            attempts = list(attempts_query.order_by('pk'))
            if len(rows) != len(attempts) or [row['attempt_id'] for row in rows] != [attempt.pk for attempt in attempts]:
                raise ProcessRequestError('submission_manifest_incomplete', status=409)
            confidence_by_attempt = {assignment.attempt_id: assignment for assignment in
                                     ConfidenceAssignment.objects.filter(attempt__session=session)}
            frozen = []
            for attempt, row in zip(attempts, rows):
                active_id = str(attempt.active_response_event_id) if attempt.active_response_event_id else None
                if row['response_version'] != attempt.response_version or row['target_event_id'] != active_id:
                    raise ProcessRequestError('response_version_conflict', status=409)
                snapshot = attempt.current_response_snapshot
                answer = snapshot.state_json.get('value', snapshot.content) if snapshot is not None else attempt.draft_answer
                has_answer = answer not in (None, '', [], {}) and (not isinstance(answer, str) or bool(answer.strip()))
                if required_tools and has_answer and attempt.status not in (
                        Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED):
                    from .research_tool_services import assert_required_tools
                    attempt.session = session
                    assert_required_tools(attempt)
                confidence = confidence_by_attempt.get(attempt.pk)
                probability = None
                missing_reason = None
                if confidence is not None and confidence.draft_answer_event_id == attempt.active_response_event_id:
                    probability = confidence.draft_probability
                if confidence is not None and confidence.condition == ConfidenceAssignment.Condition.PROMPT and probability is None:
                    missing_reason = ('answer_changed' if confidence.draft_probability is not None
                                      and confidence.draft_answer_event_id != attempt.active_response_event_id
                                      else confidence.draft_missing_reason)
                frozen.append({**row, 'response_snapshot_id': str(snapshot.pk) if snapshot else None,
                               'answer': answer, 'status': attempt.status,
                               'confidence_assignment_id': str(confidence.pk) if confidence else None,
                               'confidence_probability': probability,
                               'confidence_missing_reason': missing_reason,
                               'confidence_draft_probability': confidence.draft_probability if confidence else None,
                               'confidence_answer_event_id': str(confidence.draft_answer_event_id)
                                   if confidence and confidence.draft_answer_event_id else None})
            if SessionSubmission.objects.filter(submission_token=token).exists():
                raise ProcessRequestError('submission_token_conflict', status=409)
            job = SessionSubmission.objects.create(session=session, submission_token=token,
                material_fingerprint=fingerprint, manifest={'version': 1, 'attempts': frozen},
                accepted_at=received_at, client_clicked_at=clicked_at)
            session.status = ExperimentSession.Status.SUBMITTING
            session.terminal_at = received_at
            session.authoritative_terminal_at = received_at
            session.terminal_recorded_at = timezone.now()
            session.terminal_reason = 'session_submission_accepted'
            session.save(update_fields=['status', 'terminal_at', 'authoritative_terminal_at',
                                        'terminal_recorded_at', 'terminal_reason'])
        return job
    except IntegrityError as error:
        raise ProcessRequestError('submission_token_conflict', status=409) from error


def submission_worker_connection_is_pooled(database_connection):
    return bool(os.getenv('POSTGRES_POOL_ALIAS')
                or 'pgbouncer' in str(database_connection.settings_dict.get('HOST', '')).lower())


def session_submission_worker_status():
    """Observe a DB-connected worker across containers, without claiming execution."""
    observed_at = timezone.now().isoformat()
    # Pool backends can outlive their worker clients and retain another client's tag.
    if connection.vendor != 'postgresql' or submission_worker_connection_is_pooled(connection):
        return {'status': 'unknown', 'observed_at': observed_at}
    with connection.cursor() as cursor:
        cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() '
                       'AND application_name = %s)', [WORKER_APPLICATION_NAME])
        connected = cursor.fetchone()[0]
    return {'status': 'connected' if connected else 'not_connected', 'observed_at': observed_at}


def observe_submission_queue():
    now = timezone.now()
    queue = SessionSubmission.objects.filter(status='queued').aggregate(
        queued_count=Count('pk'), oldest_queued_at=Min('accepted_at'))
    oldest = queue['oldest_queued_at']
    return {'queued_count': queue['queued_count'],
            'oldest_queued_at': oldest.isoformat() if oldest else None,
            'oldest_queued_age_seconds': max(0, int((now - oldest).total_seconds())) if oldest else None,
            'worker': session_submission_worker_status()}


def submission_receipt(job):
    session = job.session
    next_url = reverse('assessment:complete', args=[session.pk])
    state = job.status
    if state == SessionSubmission.Status.QUEUED and job.attempts:
        state = 'retrying'
    elif state == SessionSubmission.Status.RUNNING and job.lease_expires_at <= timezone.now():
        state = 'failed' if job.attempts >= MAX_ATTEMPTS else 'recovering'
    return {'success': True, 'submission_id': str(job.pk), 'status': job.status,
            'processing_state': state, 'worker': session_submission_worker_status(),
            'accepted_at': job.accepted_at.isoformat(),
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
            'error': ({'code': 'submission_needs_verification',
                       'message': '试卷已受理，处理结果需要核验，请联系教师；请勿重复作答。'}
                      if state == SessionSubmission.Status.FAILED else None),
            'status_url': reverse('assessment_api:student-session-submission', args=[session.pk]),
            'next_url': next_url}


@_timed_submission_phase('submission_claim')
def claim_session_submission(*, worker_id):
    now = timezone.now()
    with transaction.atomic():
        # A final crash is visible instead of leaving a permanently running receipt.
        SessionSubmission.objects.filter(status='running', lease_expires_at__lte=now,
                                          attempts__gte=MAX_ATTEMPTS).update(
            status='failed', error_code='worker_lease_exhausted', lease_token=None, lease_expires_at=None)
        job = SessionSubmission.objects.select_for_update(skip_locked=True).filter(
            Q(status='queued', next_attempt_at__lte=now, attempts__lt=MAX_ATTEMPTS)
            | Q(status='running', lease_expires_at__lte=now, attempts__lt=MAX_ATTEMPTS),
        ).order_by('accepted_at').first()
        if job is None:
            return None
        job.status = SessionSubmission.Status.RUNNING
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.error_code = ''
        job.save(update_fields=['status', 'attempts', 'locked_by', 'lease_token', 'lease_expires_at', 'error_code'])
        return job


def _commit_frozen_confidence(attempt, row, now, confidence_by_attempt):
    from .confidence_services import _append_confidence_event
    assignment = confidence_by_attempt.get(attempt.pk)
    if (assignment is None or str(assignment.pk) != row['confidence_assignment_id']
            or assignment.condition != ConfidenceAssignment.Condition.PROMPT):
        return
    assignment.attempt = attempt
    if assignment.response_status in ('committed', 'skipped'):
        return
    probability = row['confidence_probability']
    if probability is None:
        assignment.response_status = ConfidenceAssignment.ResponseStatus.SKIPPED
        assignment.save(update_fields=['response_status'])
        _append_confidence_event(assignment, event_type='confidence_skipped',
            payload={'confidence_assignment_id': str(assignment.pk), 'reason_code': row['confidence_missing_reason']})
    else:
        assignment.response_status = ConfidenceAssignment.ResponseStatus.COMMITTED
        assignment.probability = probability
        assignment.committed_at = now
        assignment.save(update_fields=['response_status', 'probability', 'committed_at'])
        _append_confidence_event(assignment, event_type='confidence_committed',
            payload={'confidence_assignment_id': str(assignment.pk), 'probability': probability})


def _finalize_session(session, job, now):
    session.status = ExperimentSession.Status.COMPLETED
    session.completed_at = now
    _append_session_event(session, event_type='session_completed',
        payload={'completion_reason': 'student_finished', 'terminal_at': job.accepted_at.isoformat()})
    session.result_revision += 1
    session.save(update_fields=['status', 'completed_at', 'submission_count', 'last_activity_at',
                                'next_ingest_sequence_no', 'result_revision'])
    _schedule_research_materialization(session.pk)
    job.status = SessionSubmission.Status.COMPLETED
    job.completed_at = now
    job.lease_token = None
    job.lease_expires_at = None
    job.error_code = ''
    job.save(update_fields=['status', 'completed_at', 'lease_token', 'lease_expires_at', 'error_code'])


@_timed_submission_phase('submission_process')
def run_session_submission(claimed_job):
    """One atomic paper commit. Lease fencing excludes a superseded worker."""
    from .feedback_services import InitialGradingResult, initial_grading_result
    try:
        with coalesce_session_materializations(), transaction.atomic():
            session = ExperimentSession.objects.select_for_update(no_key=True, of=('self',)).select_related(
                'version__experiment', 'capture_contract', 'class_task__release_snapshot', 'student',
            ).get(pk=claimed_job.session_id)
            job = SessionSubmission.objects.select_for_update().get(pk=claimed_job.pk)
            if (job.status != 'running' or job.lease_token != claimed_job.lease_token
                    or job.lease_expires_at <= timezone.now()):
                return job
            if session.status != ExperimentSession.Status.SUBMITTING:
                raise RuntimeError('frozen_session_state_conflict')
            now = timezone.now()
            rows = job.manifest['attempts']
            attempts = {attempt.pk: attempt for attempt in session.attempts.select_for_update(of=('self',)).select_related(
                'assignment__snapshot', 'assignment__item_instance')}
            snapshot_ids = [row['response_snapshot_id'] for row in rows if row['response_snapshot_id']]
            snapshots = {str(snapshot.pk): snapshot for snapshot in ResponseSnapshot.objects.filter(pk__in=snapshot_ids)}
            confidence_by_attempt = {assignment.attempt_id: assignment for assignment in
                ConfidenceAssignment.objects.select_for_update(of=('self',)).filter(attempt__session=session)}
            with session_event_sequence_context(session, confidence_assignments=confidence_by_attempt) as submission_context:
                for row in rows:
                    attempt = attempts[row['attempt_id']]
                    attempt.session = session
                    if attempt.status in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED):
                        continue
                    attempt.draft_answer = row['answer']
                    attempt.current_response_snapshot = snapshots.get(row['response_snapshot_id'])
                    attempt.active_response_event_id = row['target_event_id']
                    attempt.response_version = row['response_version']
                    answer = row['answer']
                    has_answer = answer not in (None, '', [], {}) and (not isinstance(answer, str) or bool(answer.strip()))
                    if not has_answer:
                        attempt.status = Attempt.Status.UNANSWERED
                        attempt.close_reason = 'unanswered'
                        attempt.authoritative_terminal_at = job.accepted_at
                        attempt.terminal_recorded_at = now
                        attempt.is_correct = None
                        attempt.score = None
                        attempt.final_response_event_id = row['target_event_id']
                        attempt.final_response_snapshot = attempt.current_response_snapshot
                        attempt.save(update_fields=['status', 'close_reason', 'authoritative_terminal_at',
                                                   'terminal_recorded_at', 'is_correct', 'score',
                                                   'final_response_event', 'final_response_snapshot'])
                        _append_attempt_closed(attempt, close_reason='unanswered')
                        continue
                    _commit_frozen_confidence(attempt, row, now, confidence_by_attempt)
                    snapshot = attempt.assignment.snapshot
                    try:
                        if snapshot.question_type == 'numeric':
                            normalize_finite_decimal(answer)
                        correct, score = score_payload(snapshot.question_type, snapshot.correct_answer, answer,
                            accepted_answers=snapshot.accepted_answers, absolute_tolerance=snapshot.absolute_tolerance,
                            relative_tolerance=snapshot.relative_tolerance)
                        grading = initial_grading_result(attempt, correct, score)
                    except (TypeError, ValueError, ValidationError):
                        grading = InitialGradingResult(status='pending', is_correct=None, score=None,
                                                       max_score=None, method='session_unvalidated_answer_v1')
                    _commit_attempt_submission(session, attempt,
                        token_value=uuid.uuid5(job.pk, str(attempt.pk)), grading=grading,
                        correct=grading.is_correct, score=grading.score, now=now,
                        client_time=job.client_clicked_at, terminal_at=job.accepted_at,
                        close_reason='session_submitted', submission_context=submission_context,
                        server_received_at=job.accepted_at)
                _finalize_session(session, job, timezone.now())
        return job
    except Exception:
        logger.exception('Whole-session submission processing failed: %s', claimed_job.pk)
        now = timezone.now()
        SessionSubmission.objects.filter(pk=claimed_job.pk, status='running', lease_token=claimed_job.lease_token).update(
            status='queued' if claimed_job.attempts < MAX_ATTEMPTS else 'failed',
            error_code='submission_processing_failed', next_attempt_at=now + timedelta(seconds=2 ** claimed_job.attempts),
            lease_token=None, lease_expires_at=None)
        return SessionSubmission.objects.get(pk=claimed_job.pk)
