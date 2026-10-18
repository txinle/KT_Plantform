"""Move per-item initialization to publication; activate one student in bounded work."""
import copy
import hashlib
import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import CourseEnrollment, Course
from apps.experiments.models import (ClassExperimentTask, ExperimentVersionParticipantSnapshot,
                                    ItemInstance, QuestionSnapshot)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from .confidence_services import materialize_confidence_assignments
from .deadline_services import DeadlineDecisionService
from .event_services import append_assessment_event
from .models import (AssessmentEvent, Attempt, ConfidenceAssignment, ExperimentSession, QuestionVisitEvent,
                     StudentQuestionAssignment)
from .prepared_content import canonical_json, persist_display_content
from .prepared_admission import ActivationExecuting, acquire_activation_slot
from .research_tool_services import has_research_tools
from .prepared_models import (PreparedActivationCancellation, PreparedAttemptProjection,
                             PreparedSessionArtifact, PreparedSessionRepair)
from .recording_capture_services import capture_contract_for_start, text_capture_policy

PREPARATION_VERSION = 'prepared-paper-v1'


class PreparationNotReady(ValidationError):
    pass


def preparation_signature(participant):
    return hashlib.sha256(canonical_json({
        'preparation': PREPARATION_VERSION, 'version': participant.version_id,
        'participant': participant.pk, 'assignment': participant.assignment_snapshot,
        'sampling_seed': participant.sampling_seed, 'ordering_seed': participant.ordering_seed,
    })).hexdigest()


def _projection(attempt, confidence, item_count):
    from .atomic_submission_services import MAX_BYTES, MAX_CHECKPOINTS, MAX_EVENTS
    from .api.views import _runtime_options, _runtime_pending_confidence, _runtime_initial_response_state
    from .research_tool_services import available_tools, due_rules
    session, assignment = attempt.session, attempt.assignment
    tools = available_tools(attempt, events=[])
    tools['rules'] = due_rules(attempt, events=[], initial_prepared=True)
    question = {
        'snapshot_id': assignment.snapshot_id, 'stable_code': assignment.snapshot.stable_code,
        'code': assignment.snapshot.code, 'question_type': assignment.snapshot.question_type,
        'text': assignment.snapshot.text, 'primary_kc': assignment.snapshot.primary_kc if tools['kc_visible'] else '',
        'options': _runtime_options(assignment),
    }
    content = persist_display_content({'question': question})
    empty_question = {**question, 'text': '', 'options': []}
    payload = {
        'assessment_flow_version': 1,
        'offline_authorization': {'user_id': session.student_id, 'session_id': session.pk,
                                  'assignment_id': assignment.pk},
        'entry_allowed': False, 'entry_denial_code': 'SESSION_NOT_STARTED',
        'task_id': session.class_task_id, 'session_id': session.pk, 'session_status': session.status,
        'experiment_name': session.version.experiment.name, 'research_tools': tools,
        'assignment': {'assignment_id': assignment.pk, 'attempt_id': attempt.pk,
                       'status': attempt.status, 'sequence_index': assignment.assigned_sequence_index},
        'progress': {'submitted': 0, 'total': item_count}, 'remaining_seconds': None,
        'question': empty_question, 'draft_answer': None,
        'initial_response_state': _runtime_initial_response_state(attempt),
        'pending_confidence': _runtime_pending_confidence(
            attempt, confidence if confidence.condition == ConfidenceAssignment.Condition.PROMPT else None),
        'inline_confidence': {'probability': None, 'answer_event_id': None},
        'navigation': [],
        'actions': {
            'research_tools_url': reverse('assessment_process:research_tools', args=[attempt.pk]),
            'resolve_branch_url': reverse('assessment_process:resolve_branch', args=[attempt.pk]),
            'save_draft_url': reverse('assessment_api:save_draft', args=[attempt.pk]),
            'finish_url': reverse('assessment_api:finish', args=[session.pk]),
        },
        'process': {
            'paper_flow_version': 1,
            'text_capture_policy': text_capture_policy(session),
            'visit_id': None, 'visit_index': None, 'entry_event_id': None, 'navigation_id': None,
            'session_id': session.pk, 'attempt_id': attempt.pk, 'capture_version': 'capture-v2',
            'writer_lease_acquire_url': reverse('assessment_process:writer_lease_acquire', args=[attempt.pk]),
            'writer_lease_renew_url': reverse('assessment_process:writer_lease_renew', args=[attempt.pk]),
            'writer_lease_release_url': reverse('assessment_process:writer_lease_release', args=[attempt.pk]),
            'active_response_event_id': None, 'response_version': 0,
            'event_batch_url': reverse('assessment_process:capture_v2_batch'),
            'checkpoint_url': reverse('assessment_process:capture_checkpoint'),
            'time_sync_create_url': reverse('assessment_process:time_sync_create'),
            'time_sync_finalize_url': reverse('assessment_process:time_sync_finalize'),
            'app_version': 'prepared-paper-v1',
            'heartbeat_interval_seconds': session.version.configuration.get('heartbeat_interval_seconds', 30),
            'transition_entry': {'enabled': True, 'version': 1},
            'capture_bundle': {'enabled': True, 'version': 1, 'recovery_supported': True,
                               'mutation_receipts': True, 'max_events': MAX_EVENTS, 'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES},
            'close_bundle': {'enabled': True, 'version': 1, 'max_events': MAX_EVENTS,
                              'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES},
            'save_and_leave': {'enabled': True, 'version': 1, 'max_events': MAX_EVENTS,
                                'max_checkpoints': MAX_CHECKPOINTS, 'max_bytes': MAX_BYTES},
        },
    }
    if not has_research_tools(attempt):
        payload['actions'].pop('research_tools_url', None)
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder)), content


@transaction.atomic
def prepare_participant(participant):
    """Publication/repair only. No deadlines, visits or exposure are synthesized."""
    participant = ExperimentVersionParticipantSnapshot.objects.select_for_update(of=('self',)).select_related(
        'version__experiment', 'class_snapshot', 'class_task', 'student').get(pk=participant.pk)
    if not participant.class_task_id or not participant.assignment_snapshot:
        raise PreparationNotReady('prepared_assignment_missing')
    session, created = ExperimentSession.objects.get_or_create(
        class_task=participant.class_task, participant_snapshot=participant,
        defaults={
            'version': participant.version, 'student': participant.student,
            'class_snapshot': participant.class_snapshot,
            'class_snapshot_name': participant.class_snapshot.display_name,
            'sampling_seed': participant.sampling_seed, 'ordering_seed': participant.ordering_seed,
            'sampling_algorithm_version': participant.sampling_algorithm_version,
            'assignment_unit_type': participant.version.assignment_unit_type,
            'status': ExperimentSession.Status.PREPARED, 'started_at': None,
            'assessment_flow_version': 1, 'capture_contract': capture_contract_for_start(
                participant.student, participant.version.experiment, require_capture_v2=True),
            'transition_protocol_version': 1,
        },
    )
    if session.status != ExperimentSession.Status.PREPARED:
        return None
    signature = preparation_signature(participant)
    artifact, _ = PreparedSessionArtifact.objects.get_or_create(
        session=session, defaults={'preparation_version': signature})
    if artifact.ready and artifact.preparation_version == signature:
        return artifact
    ordered_ids = [item['question_snapshot_id'] for item in participant.assignment_snapshot]
    snapshots = {row.pk: row for row in QuestionSnapshot.objects.filter(
        version=participant.version, pk__in=ordered_ids)}
    if len(snapshots) != len(ordered_ids):
        raise PreparationNotReady('prepared_frozen_mapping_missing')
    existing_assignments = {row.assigned_sequence_index: row for row in session.assignments.all()}
    assignments = []
    for index, snapshot_id in enumerate(ordered_ids, 1):
        snapshot = snapshots[snapshot_id]
        item = get_or_create_default_item_instance(snapshot)
        assignment = existing_assignments.get(index)
        if assignment is None:
            assignment = StudentQuestionAssignment.objects.create(
                session=session, snapshot=snapshot, item_instance=item,
                assigned_sequence_index=index, random_seed=participant.ordering_seed)
        elif assignment.snapshot_id != snapshot_id or assignment.item_instance_id != item.pk:
            raise PreparationNotReady('prepared_frozen_mapping_conflict')
        assignment.session, assignment.snapshot, assignment.item_instance = session, snapshot, item
        assignments.append(assignment)
    existing_attempts = {row.assignment_id: row for row in session.attempts.all()}
    attempts = []
    for assignment in assignments:
        attempt = existing_attempts.get(assignment.pk)
        if attempt is None:
            attempt = Attempt.objects.create(session=session, assignment=assignment)
        attempt.session, attempt.assignment = session, assignment
        attempts.append(attempt)
    materialize_confidence_assignments(session, new_attempts=attempts)
    confidence = {row.attempt_id: row for row in ConfidenceAssignment.objects.filter(
        attempt__session=session)}
    return _persist_paper_projections(session, attempts, confidence, signature, artifact)


def _persist_paper_projections(session, attempts, confidence, signature, artifact):
    navigation, first_runtime = [], None
    for attempt in attempts:
        confidence[attempt.pk].attempt = attempt
        runtime, descriptor = _projection(attempt, confidence[attempt.pk], len(attempts))
        PreparedAttemptProjection.objects.update_or_create(
            attempt=attempt, defaults={'runtime': runtime, 'content_descriptor': descriptor})
        navigation.append({
            'assignment_id': attempt.assignment_id, 'attempt_id': attempt.pk,
            'sequence_index': attempt.assignment.assigned_sequence_index,
            'status': attempt.status, 'current': False,
            'url': reverse('assessment:question', args=[session.pk, attempt.assignment_id]),
            'response_version': attempt.response_version,
            'active_response_event_id': str(attempt.active_response_event_id) if attempt.active_response_event_id else None,
            'content': descriptor,
        })
        if first_runtime is None:
            first_runtime = runtime
    manifest = persist_display_content({
        'version': 1, 'session_id': session.pk, 'content_version': signature,
        'navigation': navigation, 'question_count': len(attempts),
    })
    first_runtime['prepared_paper'] = {
        'version': 1, 'content_version': signature, 'manifest': manifest,
        'question': navigation[0]['content'],
    }
    artifact.preparation_version, artifact.item_count = signature, len(attempts)
    artifact.first_attempt, artifact.first_runtime = attempts[0], first_runtime
    artifact.manifest_descriptor, artifact.ready = manifest, True
    artifact.save()
    return artifact


@transaction.atomic
def ensure_canonical_paper_session(session):
    """Upgrade only the presentation protocol of a compatible unfinished session.

    Frozen capture/transition contracts, question identities, answers, grading,
    deadlines and captured events remain untouched. Unsupported history requires
    teacher resolution instead of silently rewriting its measurement protocol.
    """
    if session.assessment_flow_version == 1:
        return session
    session = ExperimentSession.objects.select_for_update(of=('self',)).select_related(
        'version__experiment__course', 'class_task', 'participant_snapshot', 'capture_contract').get(pk=session.pk)
    if session.assessment_flow_version == 1:
        return session
    from .capture_v2_services import uses_capture_v2
    if (session.status != ExperimentSession.Status.IN_PROGRESS or not uses_capture_v2(session)
            or session.transition_protocol_version != 1 or not session.participant_snapshot_id):
        raise ValidationError('WHOLE_PAPER_UPGRADE_REQUIRED')
    participant = session.participant_snapshot
    assignments = list(session.assignments.select_related('snapshot', 'item_instance').order_by('assigned_sequence_index'))
    if (not assignments or participant.version_id != session.version_id
            or participant.student_id != session.student_id
            or participant.class_task_id != session.class_task_id
            or [row.snapshot_id for row in assignments] != [item['question_snapshot_id'] for item in participant.assignment_snapshot]
            or any(row.item_instance.question_snapshot_id != row.snapshot_id for row in assignments)):
        raise ValidationError('WHOLE_PAPER_UPGRADE_REQUIRED')
    by_assignment = {row.assignment_id: row for row in session.attempts.select_for_update(of=('self',)).select_related('current_response_snapshot')}
    confidence = {row.attempt_id: row for row in ConfidenceAssignment.objects.filter(attempt__session=session)}
    if (len(by_assignment) != len(assignments)
            or any(row.pk not in by_assignment or by_assignment[row.pk].pk not in confidence for row in assignments)):
        raise ValidationError('WHOLE_PAPER_UPGRADE_REQUIRED')
    attempts = []
    for assignment in assignments:
        assignment.session = session
        attempt = by_assignment[assignment.pk]
        attempt.assignment, attempt.session = assignment, session
        attempts.append(attempt)
    signature = preparation_signature(participant)
    artifact, _ = PreparedSessionArtifact.objects.get_or_create(session=session,
        defaults={'preparation_version': signature})
    session.assessment_flow_version = 1
    session.save(update_fields=['assessment_flow_version'])
    _persist_paper_projections(session, attempts, confidence, signature, artifact)
    from apps.audit.models import AuditLog
    AuditLog.objects.create(actor=session.student, action='assessment.whole_paper_upgraded',
        object_type='ExperimentSession', object_id=str(session.pk), details={
            'previous_flow_version': 0, 'flow_version': 1,
            'capture_contract_id': str(session.capture_contract_id),
            'transition_protocol_version': session.transition_protocol_version,
            'assignment_ids': [row.pk for row in assignments],
            'attempt_ids': [row.pk for row in attempts],
        })
    return session


@transaction.atomic
def enqueue_preparation_repair(participant):
    participant = ExperimentVersionParticipantSnapshot.objects.select_for_update().get(pk=participant.pk)
    job = PreparedSessionRepair.objects.get_or_create(
        participant=participant, preparation_version=f'{PREPARATION_VERSION}:{participant.version_id}')[0]
    if job.status == 'completed' and not PreparedSessionArtifact.objects.filter(
            session__participant_snapshot=participant, ready=True).exists():
        PreparedSessionRepair.objects.filter(pk=job.pk, status='completed').update(
            status='queued', attempts=0, error_code='', requested_at=timezone.now())
        job.refresh_from_db()
    return job


def run_preparation_repair():
    """One background row per transaction; crashes roll back the claim and products."""
    from apps.analytics.runtime_pressure import runtime_pressure
    if runtime_pressure()['mode'] in ('capture_priority', 'capture_emergency'):
        return False
    with transaction.atomic():
        job = PreparedSessionRepair.objects.select_for_update(skip_locked=True).filter(
            Q(status='queued') | Q(status='failed', attempts__lt=3)).order_by('requested_at').first()
        if job is None:
            return False
        job.attempts += 1
        try:
            with transaction.atomic():
                prepare_participant(job.participant)
            job.status, job.error_code = 'completed', ''
        except (ValidationError, OSError) as error:
            job.status = 'failed'
            job.error_code = ('preparation_io_error' if isinstance(error, OSError)
                              else 'preparation_frozen_mapping_invalid')
        job.save(update_fields=['status', 'attempts', 'error_code'])
    return True


def preparation_repair_status(job):
    """Queued work is not evidence that a repair worker is executing it."""
    from apps.analytics.runtime_pressure import runtime_pressure
    if job.status == 'failed' and job.attempts >= 3:
        status = 'exhausted'
    elif runtime_pressure()['mode'] in ('capture_priority', 'capture_emergency'):
        status = 'paused'
    elif job.status == 'failed':
        status = 'retrying'
    else:
        status = job.status
    return {'status': status, 'attempts': job.attempts, 'max_attempts': 3}


def save_inline_confidence(attempt, document=None):
    """Mutable draft only; confidence observations stay in the append-only client stream."""
    from .confidence_services import validate_confidence_probability
    if attempt.session.assessment_flow_version != 1:
        if document is not None:
            raise ValidationError('INLINE_CONFIDENCE_NOT_ENABLED')
        return
    if not connection.in_atomic_block:
        raise RuntimeError('inline confidence requires the parent session transaction')
    if attempt.session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError('SESSION_FROZEN')
    assignment = ConfidenceAssignment.objects.get(attempt=attempt)
    assignment.attempt = attempt
    head = str(attempt.active_response_event_id) if attempt.active_response_event_id else None
    if document is None:
        if assignment.draft_answer_event_id and str(assignment.draft_answer_event_id) != head:
            assignment.draft_probability = None
            assignment.draft_answer_event_id = None
            assignment.draft_missing_reason = 'answer_changed'
            assignment.save(update_fields=['draft_probability', 'draft_answer_event_id', 'draft_missing_reason'])
    else:
        if not isinstance(document, dict) or set(document) != {'probability', 'answer_event_id'}:
            raise ValidationError('INLINE_CONFIDENCE_INVALID')
        if document['answer_event_id'] != head:
            raise ValidationError('INLINE_CONFIDENCE_ANSWER_CHANGED')
        probability = document['probability']
        if assignment.condition != ConfidenceAssignment.Condition.PROMPT:
            if probability is not None:
                raise ValidationError('INLINE_CONFIDENCE_NOT_ASSIGNED')
            return
        if probability is not None:
            validate_confidence_probability(attempt, probability)
        changed_answer = ((str(assignment.draft_answer_event_id) if assignment.draft_answer_event_id else None) != head
                          and assignment.draft_probability is not None)
        assignment.draft_probability = probability
        assignment.draft_answer_event_id = attempt.active_response_event_id
        if probability is not None:
            assignment.draft_missing_reason = ''
        elif changed_answer:
            assignment.draft_missing_reason = 'answer_changed'
        elif not assignment.draft_missing_reason:
            assignment.draft_missing_reason = 'optional_unfilled'
        assignment.save(update_fields=['draft_probability', 'draft_answer_event_id', 'draft_missing_reason'])
    attempt._inline_confidence_receipt = {
        'probability': assignment.draft_probability,
        'answer_event_id': str(assignment.draft_answer_event_id) if assignment.draft_answer_event_id else None,
    }


def prepared_runtime(attempt, visit_entry=None, *, request_received_at=None, decision=None):
    """Read one prepared projection plus current state, never the full paper."""
    from .api.views import _runtime_initial_response_state
    session = attempt.session
    projection = PreparedAttemptProjection.objects.get(attempt=attempt)
    artifact = PreparedSessionArtifact.objects.only('manifest_descriptor', 'preparation_version',
                                                  'item_count').get(session=session)
    payload = copy.deepcopy(projection.runtime)
    payload['actions'].pop('submit_url', None)
    payload['actions'].pop('skip_url', None)
    if not has_research_tools(attempt):
        payload['actions'].pop('research_tools_url', None)
    from .research_tool_services import available_tools, due_rules
    if 'snapshot' not in attempt.assignment._state.fields_cache:
        attempt.assignment.snapshot = QuestionSnapshot.objects.only('pk', 'version_id', 'code').get(
            pk=attempt.assignment.snapshot_id)
    tool_events = list(AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool').order_by('server_ingest_sequence_no'))
    payload['research_tools'] = available_tools(attempt, events=tool_events)
    payload['research_tools']['rules'] = due_rules(attempt, events=tool_events)
    now = request_received_at or timezone.now()
    write_decision = DeadlineDecisionService.can_write_response(session.class_task, session, attempt, now)
    decision = decision or DeadlineDecisionService.can_enter_item(session.class_task, session, now)
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        from .deadline_services import DeadlineDecision
        decision = DeadlineDecision(False, 'SESSION_CLOSED')
    writable = (write_decision.allowed and session.status == ExperimentSession.Status.IN_PROGRESS
                and attempt.status not in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED))
    payload.update(session_status=session.status, entry_allowed=decision.allowed,
                   response_write_allowed=writable,
                   entry_denial_code=decision.code, draft_answer=attempt.draft_answer,
                   initial_response_state=_runtime_initial_response_state(attempt))
    payload['assignment']['status'] = attempt.status
    payload['progress'] = {'submitted': session.submission_count, 'total': artifact.item_count}
    deadline = ((attempt.current_effective_deadline_at or attempt.initial_effective_deadline_at)
                if writable else None) or session.deadline_at
    payload['presented_deadline_at'] = deadline.isoformat() if deadline else None
    payload['remaining_seconds'] = max(0, int((deadline - now).total_seconds())) if deadline else None
    payload['prepared_paper'] = {'version': 1, 'content_version': artifact.preparation_version,
                                 'manifest': artifact.manifest_descriptor, 'question': projection.content_descriptor}
    payload['process'].update(paper_flow_version=1, text_capture_policy=text_capture_policy(session),
        response_version=attempt.response_version,
        active_response_event_id=str(attempt.active_response_event_id) if attempt.active_response_event_id else None)
    confidence = ConfidenceAssignment.objects.filter(attempt=attempt).first()
    if confidence:
        payload['inline_confidence'] = {'probability': confidence.draft_probability,
            'answer_event_id': str(confidence.draft_answer_event_id) if confidence.draft_answer_event_id else None}
    if visit_entry is not None:
        payload['entry_receipt'] = {
            'navigation_id': str(visit_entry.navigation_id), 'session_id': session.pk,
            'assignment_id': attempt.assignment_id, 'attempt_id': attempt.pk,
            'visit_id': visit_entry.visit.pk, 'visit_index': visit_entry.visit.visit_index,
            'entry_event_id': str(visit_entry.entry_event.pk),
            'entered_at': visit_entry.entry_event.server_received_at.isoformat(),
        }
        payload['process'].update(visit_id=visit_entry.visit.pk, visit_index=visit_entry.visit.visit_index,
            entry_event_id=str(visit_entry.entry_event.pk), navigation_id=str(visit_entry.navigation_id))
    return payload


def activate_prepared_session(*, student, task_id, client_instance_id, navigation_id,
                             request_received_at=None, admission_token='', execution_id=None,
                             client_wall_time=None, client_monotonic_ms=None, client_time_origin=None):
    """Fixed-size activation with optional capacity in this same transaction."""
    now = request_received_at or timezone.now()
    client_id, navigation_id = uuid.UUID(str(client_instance_id)), uuid.UUID(str(navigation_id))
    transaction_admission = settings.ASSESSMENT_PREPARED_ADMISSION_MODE == 'postgres_transaction'
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)",
                               [str(settings.ASSESSMENT_ACTIVATION_STATEMENT_TIMEOUT_MS)])
                cursor.execute("SELECT set_config('lock_timeout', %s, true)",
                               [str(settings.ASSESSMENT_ACTIVATION_LOCK_TIMEOUT_MS)])
        try:
            session = ExperimentSession.objects.select_for_update(of=('self',), no_key=True,
                nowait=transaction_admission).select_related(
                'class_task', 'version__experiment__course', 'capture_contract').get(
                    class_task_id=task_id, student=student, assessment_flow_version=1)
        except DatabaseError as error:
            cause = error.__cause__
            if transaction_admission and getattr(cause, 'sqlstate', None) == '55P03':
                raise ActivationExecuting() from error
            raise
        artifact = PreparedSessionArtifact.objects.get(session=session)
        if transaction_admission and not artifact.activation_receipt:
            acquire_activation_slot(student_id=student.pk)
            execution_id = execution_id or uuid.uuid4()
        from apps.accounts.research_consent import research_consent_required
        if research_consent_required(student):
            raise ValidationError('RESEARCH_CONSENT_REQUIRED')
        if not transaction_admission and execution_id and PreparedActivationCancellation.objects.filter(
            execution_id=execution_id, session=session, admission_token=admission_token).exists():
            raise ValidationError('ACTIVATION_EXECUTION_CANCELLED')
        if not artifact.ready or not artifact.first_attempt_id:
            raise PreparationNotReady('PREPARATION_NOT_READY')
        course = session.version.experiment.course
        from apps.accounts.enrollment_access import has_course_access
        if course and (course.status != Course.Status.ACTIVE or not has_course_access(
            course_id=course.pk, student_id=student.pk, at=now)):
            raise ValidationError('COURSE_ACCESS_DENIED')
        if not ExperimentVersionParticipantSnapshot.objects.filter(
            pk=session.participant_snapshot_id, class_task=session.class_task,
            version_id=session.version_id, student=student,
            class_snapshot_id=session.class_task.class_snapshot_id).exists():
            raise ValidationError('PARTICIPANT_NOT_AUTHORIZED')
        if session.status != ExperimentSession.Status.PREPARED:
            if artifact.activation_receipt:
                receipt = copy.deepcopy(artifact.activation_receipt)
                first = Attempt.objects.select_related('current_response_snapshot', 'assignment').get(
                    pk=artifact.first_attempt_id, session=session)
                first.session = session
                initial = receipt['runtime']
                runtime = prepared_runtime(first, request_received_at=now)
                runtime['entry_receipt'] = initial['entry_receipt']
                runtime['process'].update({field: initial['process'][field] for field in (
                    'visit_id', 'visit_index', 'entry_event_id', 'navigation_id')})
                lease = initial.get('initial_writer_lease')
                if (lease and artifact.activation_client_id == client_id
                        and first.writer_client_instance_id == client_id
                        and runtime['response_write_allowed']
                        and str(first.writer_lease_token) == lease['writer_lease_token']
                        and first.writer_lease_expires_at and first.writer_lease_expires_at > now
                        and session.status == ExperimentSession.Status.IN_PROGRESS):
                    runtime['initial_writer_lease'] = {**lease, 'expires_at': first.writer_lease_expires_at.isoformat()}
                receipt['runtime'] = runtime
                return session, receipt
            raise ValidationError('SESSION_NOT_ACTIVATABLE')
        task = session.class_task
        decision = DeadlineDecisionService.can_start_session(task, now)
        if not decision.allowed:
            raise ValidationError(decision.code)
        schedule = DeadlineDecisionService.schedule_at(task, now)
        config = session.version.configuration
        total = schedule.total_time_limit_seconds if schedule and schedule.total_time_limit_seconds is not None else (
            int(config.get('total_time_limit_minutes') or 0) * 60 or None)
        session.started_at, session.status = now, ExperimentSession.Status.IN_PROGRESS
        session.schedule_revision = schedule
        session.task_opens_at = schedule.opens_at if schedule else None
        session.task_closes_at = schedule.closes_at if schedule else None
        session.total_time_limit_seconds = total
        session.personal_deadline_at = now + timedelta(seconds=total) if total else None
        session.deadline_at = DeadlineDecisionService.initial_session_deadline(
            session.task_closes_at, session.personal_deadline_at)
        session.initial_effective_deadline_at = session.deadline_at
        session.timer_semantics_version = 'prepared-activation-v1'
        attempt = Attempt.objects.select_for_update(of=('self',), no_key=True).select_related(
            'assignment').get(pk=artifact.first_attempt_id, session=session)
        attempt.session = session
        assignment = attempt.assignment
        assignment.session = session
        assignment.snapshot = QuestionSnapshot.objects.only('pk', 'version_id').get(pk=assignment.snapshot_id)
        assignment.item_instance = ItemInstance.objects.only('pk', 'question_snapshot_id').get(pk=assignment.item_instance_id)
        session.current_assignment = assignment
        session.first_open_count, session.visit_event_count = 1, 1
        session.save(update_fields=['status', 'started_at', 'schedule_revision', 'task_opens_at',
            'task_closes_at', 'total_time_limit_seconds', 'personal_deadline_at', 'deadline_at',
            'initial_effective_deadline_at', 'timer_semantics_version', 'current_assignment',
            'first_open_count', 'visit_event_count', 'last_activity_at'])
        attempt.status, attempt.first_opened_at, attempt.last_opened_at = Attempt.Status.VISITED, now, now
        attempt.first_open_sequence_index, attempt.visit_count, attempt.item_started_at = 1, 1, now
        limit = (schedule.per_question_time_limit_seconds if schedule else None) or config.get('per_question_time_limit_seconds')
        attempt.per_item_time_limit_seconds = int(limit) if limit else None
        attempt.item_deadline_at = DeadlineDecisionService.item_deadline_from_start(now, attempt.per_item_time_limit_seconds)
        attempt.initial_effective_deadline_at = DeadlineDecisionService.initial_item_deadline(
            session.task_closes_at, session.deadline_at, attempt.item_deadline_at)
        attempt.current_effective_deadline_at = attempt.initial_effective_deadline_at
        attempt.writer_lease_token, attempt.writer_client_instance_id = uuid.uuid4(), client_id
        attempt.writer_lease_expires_at = now + timedelta(seconds=45)
        attempt.save()
        visit = QuestionVisitEvent.objects.create(session=session, attempt=attempt, visit_index=1,
            event_sequence=1, entry_reason='start', client_occurred_at=client_wall_time)
        append_assessment_event(event_type='session_started', event_version=2,
            event_domain='assessment', actor_source='server', session=session,
            payload={'capture_contract_id': str(session.capture_contract_id),
                     'capture_contract_sha256': session.capture_contract.schema_sha256},
            capture_mode='native', app_version='server', session_lock_held=True)
        entry_event = append_assessment_event(event_type='item_entered', event_version=3,
            event_domain='assessment', actor_source='server', session=session,
            assignment=assignment, attempt=attempt, correlation_id=navigation_id,
            question_snapshot=assignment.snapshot, item_instance=assignment.item_instance,
            payload={'entry_reason': 'start', 'from_assignment_id': None, 'visit_id': visit.pk},
            client_wall_time_utc=client_wall_time, client_monotonic_ms=client_monotonic_ms,
            client_time_origin=client_time_origin, capture_mode='native', app_version='server',
            session_lock_held=True)
        runtime = copy.deepcopy(artifact.first_runtime)
        runtime['actions'].pop('submit_url', None)
        runtime['actions'].pop('skip_url', None)
        if not has_research_tools(attempt):
            runtime['actions'].pop('research_tools_url', None)
        runtime.update(entry_allowed=True, response_write_allowed=True, entry_denial_code='', session_status=session.status)
        runtime['assignment']['status'] = attempt.status
        deadline = attempt.current_effective_deadline_at
        runtime['remaining_seconds'] = max(0, int((deadline - now).total_seconds())) if deadline else None
        runtime['presented_deadline_at'] = deadline.isoformat() if deadline else None
        runtime['entry_receipt'] = {'navigation_id': str(navigation_id), 'session_id': session.pk,
            'assignment_id': assignment.pk, 'attempt_id': attempt.pk, 'visit_id': visit.pk,
            'visit_index': 1, 'entry_event_id': str(entry_event.pk), 'entered_at': now.isoformat()}
        runtime['process'].update(paper_flow_version=1, text_capture_policy=text_capture_policy(session),
            visit_id=visit.pk, visit_index=1,
            entry_event_id=str(entry_event.pk), navigation_id=str(navigation_id))
        runtime['initial_writer_lease'] = {'writer_lease_token': str(attempt.writer_lease_token),
            'writer_client_instance_id': str(client_id), 'expires_at': attempt.writer_lease_expires_at.isoformat()}
        receipt = {'task_id': task_id, 'session_id': session.pk,
            'question_url': reverse('assessment:question', args=[session.pk, assignment.pk]),
            'runtime': runtime, 'admission_execution_id': str(execution_id) if execution_id else None}
        artifact.activation_receipt, artifact.activation_client_id = receipt, client_id
        artifact.activation_execution_id = execution_id
        artifact.admission_token = admission_token
        artifact.save(update_fields=['activation_receipt', 'activation_client_id',
                                     'activation_execution_id', 'admission_token'])
    return session, receipt
