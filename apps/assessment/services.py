from __future__ import annotations
import hashlib
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentStudent,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.services import derive_session_seeds, ordered_snapshots, select_snapshots
from apps.question_bank.services.numeric import normalize_finite_decimal
from apps.question_bank.services.scoring import score_payload
from .event_services import append_assessment_event
from .confidence_services import (
    ensure_confidence_assignment,
    materialize_confidence_assignments,
)
from .response_snapshot_services import (
    CHOICE_QUESTION_TYPES,
    TEXT_LIKE_QUESTION_TYPES,
    ResponseState,
    canonical_choice_response_state,
    create_response_snapshot,
    response_snapshot_content,
)
from .writer_lease_services import assert_attempt_writer_lease
from .recording_capture_services import ensure_capture_contract_v1
from .models import (
    AssessmentEvent,
    Attempt,
    DraftSaveEvent,
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from .deadline_services import DeadlineDecisionService


def _lock_session_course(course_id):
    from apps.accounts.models import Course

    return Course.objects.select_for_update().get(pk=course_id)


class SubmissionValidationError(ValidationError):
    def __init__(self, message, *, reason_code):
        self.reason_code = reason_code
        super().__init__(message)


def _append_session_event(session, *, event_type, payload):
    existing = AssessmentEvent.objects.filter(
        session=session,
        event_type=event_type,
    ).first()
    if existing is not None:
        return existing
    return append_assessment_event(
        event_type=event_type,
        event_version=2 if session.capture_contract_id else 1,
        event_domain="assessment",
        actor_source="server",
        session=session,
        payload=payload,
        capture_mode="native",
        app_version="server",
    )


def _append_attempt_closed(attempt, *, close_reason):
    existing = AssessmentEvent.objects.filter(
        attempt=attempt,
        event_type="attempt_closed",
    ).first()
    if existing is not None:
        return existing
    return append_assessment_event(
        event_type="attempt_closed",
        event_version=1,
        event_domain="assessment",
        actor_source="server",
        session=attempt.session,
        assignment=attempt.assignment,
        attempt=attempt,
        question_snapshot=attempt.assignment.snapshot,
        item_instance=attempt.assignment.item_instance,
        payload={"close_reason": close_reason},
        capture_mode="native",
        app_version="server",
    )


def _close_open_attempts(
    session,
    *,
    status,
    close_reason,
    event_close_reason=None,
    authoritative_terminal_at=None,
):
    attempts = list(
        session.attempts.select_for_update()
        .filter(
            status__in=[
                Attempt.Status.UNVISITED,
                Attempt.Status.VISITED,
                Attempt.Status.DRAFT,
                Attempt.Status.SKIPPED,
            ]
        )
        .select_related("assignment__snapshot", "assignment__item_instance")
    )
    for attempt in attempts:
        attempt.status = status
        attempt.is_correct = None
        attempt.close_reason = close_reason
        attempt.authoritative_terminal_at = authoritative_terminal_at
        attempt.terminal_recorded_at = timezone.now()
        attempt.save(
            update_fields=[
                "status",
                "is_correct",
                "close_reason",
                "authoritative_terminal_at",
                "terminal_recorded_at",
            ]
        )
        _append_attempt_closed(
            attempt,
            close_reason=event_close_reason or close_reason,
        )
    return attempts

@transaction.atomic
def get_or_create_session(
    experiment,
    student,
    *,
    request_received_at=None,
    class_task=None,
):
    request_received_at = request_received_at or timezone.now()
    course_id = (
        Experiment.objects.filter(pk=experiment.pk)
        .values_list("course_id", flat=True)
        .get()
    )
    course = None
    if course_id:
        from apps.accounts.models import Course, CourseEnrollment

        # Publishing and student entry share one global order: Course -> Experiment.
        course = _lock_session_course(course_id)
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.course_id != course_id:
        raise ValidationError("实验所属课程已变化，请重试")
    if experiment.course_id:
        if course.status != Course.Status.ACTIVE:
            raise ValidationError("课程已归档，不能开始或继续实验")

        if not CourseEnrollment.objects.select_for_update().filter(
            course_id=experiment.course_id,
            student=student,
            is_active=True,
        ).exists():
            raise ValidationError("你没有加入该实验所属课程")
    if experiment.status not in {Experiment.Status.PUBLISHED, Experiment.Status.FROZEN}:
        raise ValidationError("实验尚未开放")
    if class_task is not None:
        class_task = (
            ClassExperimentTask.objects.select_for_update()
            .select_related("release_snapshot")
            .get(pk=class_task.pk, experiment=experiment)
        )
        version = class_task.release_snapshot
    else:
        version = (
            ExperimentVersion.objects.select_for_update().get(
                pk=experiment.frozen_release_snapshot_id
            )
            if experiment.frozen_release_snapshot_id
            else ExperimentVersion.objects.select_for_update()
            .filter(experiment=experiment)
            .order_by("-version_number")
            .first()
        )
    participant_snapshots = (
        ExperimentVersionParticipantSnapshot.objects.select_related(
            "class_snapshot", "class_task"
        )
        .filter(version=version, student=student)
    )
    if class_task is not None:
        participant_snapshots = participant_snapshots.filter(class_task=class_task)
    if experiment.status == Experiment.Status.FROZEN:
        participant_snapshots = participant_snapshots.filter(class_task__isnull=False)
    participant_snapshot = participant_snapshots.order_by("class_task_id", "pk").first()
    # A published v2 course experiment freezes an explicit roster and must
    # never admit a student outside it.  Older/no-roster experiments have no
    # participant snapshot rows at all; retain their historical admission
    # semantics instead of retroactively making their sessions impossible.
    participant_snapshot_enabled = (
        experiment.course_id is not None
        and ExperimentVersionParticipantSnapshot.objects.filter(version=version).exists()
    )
    if participant_snapshot_enabled and participant_snapshot is None:
        raise ValidationError("该实验版本缺少参与者快照，不能开始作答")
    schedule = None
    if participant_snapshot is not None and participant_snapshot.class_task_id is not None:
        if class_task is None:
            class_task = ClassExperimentTask.objects.select_for_update().get(
                pk=participant_snapshot.class_task_id
            )
        decision = DeadlineDecisionService.can_start_session(
            class_task,
            request_received_at,
        )
        if not decision.allowed:
            raise ValidationError("当前班级任务尚未开放或已结束")
        schedule = class_task.schedule_revisions.order_by("-revision_no").first()
        existing = ExperimentSession.objects.filter(
            class_task=class_task,
            participant_snapshot=participant_snapshot,
        ).first()
        if existing is not None:
            return existing
    elif not ExperimentStudent.objects.filter(experiment=experiment, student=student).exists():
        raise ValidationError("你没有参加该实验的权限")
    if participant_snapshot is not None and participant_snapshot.sampling_seed:
        sampling_seed = participant_snapshot.sampling_seed
        ordering_seed = participant_snapshot.ordering_seed
        algorithm_version = participant_snapshot.sampling_algorithm_version
    else:
        sampling_seed, ordering_seed, algorithm_version = derive_session_seeds(
            version,
            student.id,
        )
    now = timezone.now()
    total_seconds = None
    if version.configuration.get("total_time_limit_minutes"):
        total_seconds = int(version.configuration["total_time_limit_minutes"]) * 60
    total_deadline = now + timedelta(seconds=total_seconds) if total_seconds else None
    task_close = schedule.closes_at if schedule is not None else None
    initial_deadline = DeadlineDecisionService.initial_session_deadline(
        task_close,
        total_deadline,
    )
    session_defaults = {
            "class_snapshot": (
                participant_snapshot.class_snapshot
                if participant_snapshot is not None
                else None
            ),
            "class_snapshot_name": (
                participant_snapshot.class_snapshot.display_name
                if participant_snapshot is not None
                and participant_snapshot.class_snapshot_id is not None
                else ""
            ),
            "sampling_seed": sampling_seed,
            "ordering_seed": ordering_seed,
            "sampling_algorithm_version": algorithm_version,
            "assignment_unit_type": version.assignment_unit_type,
            "capture_contract": ensure_capture_contract_v1(),
            "class_task": class_task,
            "participant_snapshot": participant_snapshot,
            "schedule_revision": schedule,
            "task_opens_at": schedule.opens_at if schedule is not None else None,
            "task_closes_at": task_close,
            "total_time_limit_seconds": total_seconds,
            "deadline_at": initial_deadline,
            "initial_effective_deadline_at": initial_deadline,
            "timer_semantics_version": version.timer_semantics_version,
        }
    if class_task is not None:
        session = ExperimentSession.objects.create(
            version=version,
            student=student,
            **session_defaults,
        )
        created = True
    else:
        session, created = ExperimentSession.objects.get_or_create(
            version=version,
            student=student,
            defaults=session_defaults,
        )
    if created:
        if participant_snapshot is not None and participant_snapshot.assignment_snapshot:
            snapshot_ids = [
                item["question_snapshot_id"]
                for item in participant_snapshot.assignment_snapshot
            ]
            snapshots_by_id = {
                snapshot.pk: snapshot
                for snapshot in QuestionSnapshot.objects.filter(
                    version=version,
                    pk__in=snapshot_ids,
                )
            }
            ordered = [snapshots_by_id[snapshot_id] for snapshot_id in snapshot_ids]
        else:
            selected = select_snapshots(version, student.id, sampling_seed)
            ordered = ordered_snapshots(version, student.id, selected, ordering_seed)
        for index, snapshot in enumerate(ordered, start=1):
            item_instance = get_or_create_default_item_instance(snapshot)
            assignment = StudentQuestionAssignment.objects.create(
                session=session, snapshot=snapshot, assigned_sequence_index=index,
                random_seed=ordering_seed,
                item_instance=item_instance,
            )
            Attempt.objects.create(session=session, assignment=assignment)
    materialize_confidence_assignments(session)
    if created:
        _append_session_event(
            session,
            event_type="session_started",
            payload={
                "capture_contract_id": str(session.capture_contract_id),
                "capture_contract_sha256": session.capture_contract.schema_sha256,
            },
        )
    return session


@transaction.atomic
def resume_task_session(task, student, *, request_received_at=None):
    request_received_at = request_received_at or timezone.now()
    task = ClassExperimentTask.objects.select_for_update().get(pk=task.pk)
    participant = ExperimentVersionParticipantSnapshot.objects.select_for_update().get(
        class_task=task,
        version=task.release_snapshot,
        student=student,
    )
    session = (
        ExperimentSession.objects.select_for_update()
        .filter(
            class_task=task,
            participant_snapshot=participant,
            student=student,
        )
        .first()
    )
    if session is None:
        raise ExperimentSession.DoesNotExist
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("该实验会话已结束，不能继续作答")
    decision = DeadlineDecisionService.can_enter_item(
        task,
        session,
        request_received_at,
    )
    if not decision.allowed:
        raise ValidationError("当前班级任务已结束，不能继续作答")
    materialize_confidence_assignments(session)
    return session

def _close_previous(session, now):
    previous = session.current_assignment
    if not previous: return
    attempt = Attempt.objects.select_for_update().get(assignment=previous)
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}: return
    if session.timer_semantics_version == "continuous_wall_clock_v2":
        return
    if attempt.last_opened_at:
        elapsed = max(0, int((now - attempt.last_opened_at).total_seconds()))
        attempt.active_seconds += elapsed
        limit = session.version.configuration.get("per_question_time_limit_seconds")
        if session.version.configuration.get("time_mode") == "per_question" and limit and attempt.active_seconds >= int(limit):
            attempt.status = Attempt.Status.TIMED_OUT
            attempt.is_correct = None
        attempt.save(update_fields=["active_seconds", "status", "is_correct"])
        if attempt.status == Attempt.Status.TIMED_OUT:
            _append_attempt_closed(attempt, close_reason="timed_out")


def _validate_course_access(session):
    experiment = session.version.experiment
    if experiment.course_id is None:
        return
    from apps.accounts.models import Course, CourseEnrollment

    course = Course.objects.select_for_update().get(pk=experiment.course_id)
    if course.status != Course.Status.ACTIVE:
        raise ValidationError("课程已归档，历史会话只读")

    if not CourseEnrollment.objects.select_for_update().filter(
        course_id=experiment.course_id,
        student_id=session.student_id,
        is_active=True,
    ).exists():
        raise ValidationError("你已不在该实验所属课程中")


def _terminate_for_deadline(session, *, now, reason, attempt=None):
    if attempt is not None:
        attempt.status = Attempt.Status.TIMED_OUT
        attempt.is_correct = None
        attempt.close_reason = reason
        attempt.authoritative_terminal_at = now
        attempt.terminal_recorded_at = timezone.now()
        attempt.save(
            update_fields=[
                "status",
                "is_correct",
                "close_reason",
                "authoritative_terminal_at",
                "terminal_recorded_at",
            ]
        )
        _append_attempt_closed(attempt, close_reason="timed_out")
        return
    _close_open_attempts(
        session,
        status=Attempt.Status.UNANSWERED,
        close_reason=reason,
        event_close_reason="unanswered",
        authoritative_terminal_at=now,
    )
    session.status = ExperimentSession.Status.EXPIRED
    session.completed_at = now
    session.terminal_at = now
    session.terminal_reason = reason
    session.authoritative_terminal_at = now
    session.terminal_recorded_at = timezone.now()
    session.save(
        update_fields=[
            "status",
            "completed_at",
            "terminal_at",
            "terminal_reason",
            "authoritative_terminal_at",
            "terminal_recorded_at",
        ]
    )
    expiry_payload = (
        {"expiration_reason": reason, "terminal_at": now.isoformat()}
        if session.capture_contract_id
        else {"expiry_reason": reason}
    )
    _append_session_event(
        session,
        event_type="session_expired",
        payload=expiry_payload,
    )


def _validate_session_time(session, attempt=None, *, request_received_at=None):
    _validate_course_access(session)
    now = request_received_at or timezone.now()
    if session.class_task_id:
        task = ClassExperimentTask.objects.select_for_update().get(pk=session.class_task_id)
        # A committed task-level termination is authoritative even when the
        # manual-close transaction has already projected the Session/Attempt
        # to an expired state.
        if task.cancelled_at or task.manual_closed_at or task.invalidated_at:
            return "班级任务已结束"
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    if session.class_task_id:
        decision = (
            DeadlineDecisionService.can_write_response(task, session, attempt, now)
            if attempt is not None
            else DeadlineDecisionService.can_enter_item(task, session, now)
        )
        if not decision.allowed:
            if decision.code == "DEADLINE_EXPIRED":
                _terminate_for_deadline(
                    session,
                    now=decision.deadline or now,
                    reason="deadline_expired",
                    attempt=attempt,
                )
                return "作答时间已结束"
            if decision.code == "TASK_TERMINATED":
                _terminate_for_deadline(
                    session,
                    now=task.manual_closed_at or task.cancelled_at or task.invalidated_at or now,
                    reason="task_terminated",
                )
                return "班级任务已结束"
            return "当前班级任务尚未开放或已结束"
    if session.deadline_at and now >= session.deadline_at:
        _terminate_for_deadline(session, now=now, reason="total_time_limit")
        return "实验总时间已结束"
    if attempt and attempt.initial_effective_deadline_at and now >= attempt.initial_effective_deadline_at:
        _terminate_for_deadline(session, now=attempt.initial_effective_deadline_at, reason="item_time_limit", attempt=attempt)
        return "本题作答时间已结束"
    if attempt and session.version.configuration.get("time_mode") == "per_question":
        limit = session.version.configuration.get("per_question_time_limit_seconds")
        elapsed = attempt.active_seconds
        if attempt.last_opened_at: elapsed += max(0, int((now - attempt.last_opened_at).total_seconds()))
        if limit and elapsed >= int(limit):
            attempt.status = Attempt.Status.TIMED_OUT; attempt.is_correct = None
            attempt.save(update_fields=["status", "is_correct"])
            _append_attempt_closed(attempt, close_reason="timed_out")
            return "本题作答时间已结束"
    return None

@transaction.atomic
def _record_visit_transaction(session_id, assignment_id, entry_reason="navigation_click", client_time=None, request_received_at=None):
    task_id = ExperimentSession.objects.only("class_task_id").get(pk=session_id).class_task_id
    if task_id:
        ClassExperimentTask.objects.select_for_update().get(pk=task_id)
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    request_received_at = request_received_at or timezone.now()
    time_error = _validate_session_time(session, request_received_at=request_received_at)
    if time_error:
        return None, time_error
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    assignment = StudentQuestionAssignment.objects.select_related("snapshot").get(pk=assignment_id, session=session)
    now = timezone.now(); from_assignment = session.current_assignment
    _close_previous(session, now)
    attempt = Attempt.objects.select_for_update().get(assignment=assignment)
    if attempt.status not in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        if attempt.first_opened_at is None:
            session.first_open_count += 1
            attempt.first_open_sequence_index = session.first_open_count
            attempt.first_opened_at = now
            if session.class_task_id:
                limit = session.version.configuration.get("per_question_time_limit_seconds")
                attempt.item_started_at = now
                attempt.per_item_time_limit_seconds = int(limit) if limit else None
                attempt.item_deadline_at = DeadlineDecisionService.item_deadline_from_start(
                    now,
                    attempt.per_item_time_limit_seconds,
                )
                attempt.initial_effective_deadline_at = (
                    DeadlineDecisionService.initial_item_deadline(
                        session.task_closes_at,
                        session.deadline_at,
                        attempt.item_deadline_at,
                    )
                )
        attempt.last_opened_at = now
        attempt.visit_count += 1
        if attempt.status in {Attempt.Status.UNVISITED, Attempt.Status.SKIPPED}: attempt.status = Attempt.Status.VISITED
        attempt.save()
    session.visit_event_count += 1
    QuestionVisitEvent.objects.create(
        session=session, attempt=attempt, from_assignment=from_assignment,
        visit_index=attempt.visit_count, event_sequence=session.visit_event_count,
        entry_reason=entry_reason, client_occurred_at=client_time,
    )
    session.current_assignment = assignment
    session.save(update_fields=["current_assignment", "first_open_count", "visit_event_count", "last_activity_at"])
    append_assessment_event(
        event_type="item_entered",
        event_version=2 if session.capture_contract_id else 1,
        event_domain="assessment",
        actor_source="server",
        session=session,
        assignment=assignment,
        attempt=attempt,
        question_snapshot=assignment.snapshot,
        item_instance=assignment.item_instance,
        payload={
            "entry_reason": entry_reason,
            "from_assignment_id": (
                from_assignment.pk if from_assignment is not None else None
            ),
        },
        capture_mode="native",
        app_version="server",
    )
    return attempt, None


def record_visit(session_id, assignment_id, entry_reason="navigation_click", client_time=None, *, request_received_at=None):
    attempt, time_error = _record_visit_transaction(
        session_id,
        assignment_id,
        entry_reason,
        client_time,
        request_received_at,
    )
    if time_error:
        raise ValidationError(time_error)
    return attempt

@transaction.atomic
def _save_draft_transaction(
    attempt_id,
    answer,
    client_time=None,
    writer_lease_token=None,
    writer_client_instance_id=None,
    request_received_at=None,
):
    session_id, task_id = Attempt.objects.filter(pk=attempt_id).values_list(
        "session_id", "session__class_task_id"
    ).get()
    if task_id:
        ClassExperimentTask.objects.select_for_update().get(pk=task_id)
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    attempt = Attempt.objects.select_for_update().select_related(
        "assignment__snapshot",
    ).get(pk=attempt_id, session=session)
    assert_attempt_writer_lease(
        attempt,
        lease_token=writer_lease_token,
        client_instance_id=writer_client_instance_id,
    )
    time_error = _validate_session_time(
        session,
        attempt,
        request_received_at=request_received_at or timezone.now(),
    )
    if time_error:
        return None, time_error
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能修改")
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    attempt.draft_answer = answer; attempt.draft_saved_at = timezone.now(); attempt.status = Attempt.Status.DRAFT
    # Clear stale pointer before creating a replacement snapshot.  The whole
    # transaction rolls back if snapshot persistence fails.
    attempt.current_response_snapshot = None
    attempt.save(update_fields=["draft_answer", "draft_saved_at", "status", "current_response_snapshot"])
    digest = hashlib.sha256(repr(answer).encode()).hexdigest()
    DraftSaveEvent.objects.create(attempt=attempt, answer_hash=digest, client_occurred_at=client_time)
    question_type = attempt.assignment.snapshot.question_type
    if question_type in TEXT_LIKE_QUESTION_TYPES:
        snapshot = create_response_snapshot(
            attempt=attempt,
            content=response_snapshot_content(answer),
        )
        attempt.current_response_snapshot = snapshot
        attempt.save(update_fields=["current_response_snapshot"])
    elif question_type in CHOICE_QUESTION_TYPES:
        # Choice state is derived from durable draft_answer; no second mutable
        # response-state column is maintained.
        canonical_choice_response_state(question_type, answer)
    return attempt, None


def save_draft(
    attempt_id,
    answer,
    client_time=None,
    *,
    writer_lease_token=None,
    writer_client_instance_id=None,
    request_received_at=None,
):
    attempt, time_error = _save_draft_transaction(
        attempt_id,
        answer,
        client_time,
        writer_lease_token,
        writer_client_instance_id,
        request_received_at,
    )
    if time_error:
        raise ValidationError(time_error)
    return attempt

@transaction.atomic
def _submit_attempt_transaction(
    attempt_id,
    token,
    client_time=None,
    writer_lease_token=None,
    writer_client_instance_id=None,
    request_received_at=None,
):
    session_id, task_id = Attempt.objects.filter(pk=attempt_id).values_list(
        "session_id", "session__class_task_id"
    ).get()
    if task_id:
        ClassExperimentTask.objects.select_for_update().get(pk=task_id)
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    attempt = Attempt.objects.select_for_update().select_related(
        "assignment__snapshot"
    ).get(pk=attempt_id, session=session)
    assert_attempt_writer_lease(
        attempt,
        lease_token=writer_lease_token,
        client_instance_id=writer_client_instance_id,
    )
    try:
        _validate_course_access(session)
    except ValidationError as error:
        raise SubmissionValidationError(
            error.messages,
            reason_code="unauthorized",
        ) from error
    if attempt.status == Attempt.Status.SUBMITTED:
        if str(attempt.submission_token) == str(token):
            return attempt, None
        raise SubmissionValidationError(
            "该题已经提交",
            reason_code="already_submitted",
        )
    time_error = _validate_session_time(
        session,
        attempt,
        request_received_at=request_received_at or timezone.now(),
    )
    if time_error:
        return None, time_error
    if attempt.status in {Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise SubmissionValidationError(
            "该题当前状态不能提交",
            reason_code=(
                "time_expired"
                if attempt.status == Attempt.Status.TIMED_OUT
                else "session_closed"
            ),
        )
    if attempt.draft_answer in (None, "", []):
        raise SubmissionValidationError(
            "请先填写答案",
            reason_code="invalid_answer",
        )
    try: token_value = uuid.UUID(str(token))
    except ValueError:
        raise SubmissionValidationError(
            "提交令牌无效",
            reason_code="conflict",
        ) from None
    snapshot = attempt.assignment.snapshot
    if snapshot.question_type == "numeric":
        try:
            normalize_finite_decimal(attempt.draft_answer)
        except ValueError:
            raise SubmissionValidationError(
                "数值题答案必须是有效数字",
                reason_code="invalid_answer",
            ) from None
    correct, score = score_payload(
        snapshot.question_type, snapshot.correct_answer, attempt.draft_answer,
        accepted_answers=snapshot.accepted_answers,
        absolute_tolerance=snapshot.absolute_tolerance,
        relative_tolerance=snapshot.relative_tolerance,
    )
    now = timezone.now(); session.submission_count += 1
    attempt.status = Attempt.Status.SUBMITTED; attempt.final_answer = attempt.draft_answer
    if attempt.current_response_snapshot is None:
        question_type = snapshot.question_type
        if question_type in CHOICE_QUESTION_TYPES:
            canonical_state = canonical_choice_response_state(
                question_type,
                attempt.draft_answer,
            )
            attempt.current_response_snapshot = create_response_snapshot(
                attempt=attempt,
                content=response_snapshot_content(canonical_state),
                response_state=ResponseState(question_type, canonical_state),
            )
    attempt.final_response_snapshot = attempt.current_response_snapshot
    attempt.submitted_at = now; attempt.submission_sequence_index = session.submission_count
    attempt.is_correct = correct; attempt.score = score; attempt.submission_token = token_value
    if attempt.first_opened_at: attempt.first_to_submit_duration_ms = int((now - attempt.first_opened_at).total_seconds() * 1000)
    if attempt.last_opened_at: attempt.last_visit_to_submission_ms = int((now - attempt.last_opened_at).total_seconds() * 1000)
    attempt.save()
    session.save(update_fields=["submission_count", "last_activity_at"])
    submission = SubmissionEvent.objects.create(
        attempt=attempt, submission_token=token_value, final_answer=attempt.final_answer,
        final_response_snapshot=attempt.final_response_snapshot,
        grading_status=SubmissionEvent.GradingStatus.COMPLETED,
        is_correct=correct, score=score, client_clicked_at=client_time,
        server_received_at=now, committed_at=now,
    )
    GradeRecord.objects.create(
        submission=submission,
        version=1,
        status=GradeRecord.Status.COMPLETED,
        validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        is_correct=correct,
        score=score,
        max_score=1,
        graded_at=now,
    )
    append_assessment_event(
        event_type="response_committed",
        event_version=1,
        event_domain="assessment",
        actor_source="server",
        session=session,
        assignment=attempt.assignment,
        attempt=attempt,
        question_snapshot=attempt.assignment.snapshot,
        item_instance=attempt.assignment.item_instance,
        correlation_id=token_value,
        payload={
            "submission_token": str(token_value),
            "submission_id": submission.pk,
            "grading_status": submission.grading_status,
            "final_response_snapshot_id": (
                str(submission.final_response_snapshot_id)
                if submission.final_response_snapshot_id is not None
                else None
            ),
            "committed_at": now.isoformat(),
        },
        capture_mode="native",
        app_version="server",
    )
    append_assessment_event(
        event_type="attempt_closed",
        event_version=1,
        event_domain="assessment",
        actor_source="server",
        session=session,
        assignment=attempt.assignment,
        attempt=attempt,
        question_snapshot=attempt.assignment.snapshot,
        item_instance=attempt.assignment.item_instance,
        correlation_id=token_value,
        payload={"close_reason": "submitted"},
        capture_mode="native",
        app_version="server",
    )
    ensure_confidence_assignment(attempt)
    return attempt, None


@transaction.atomic
def grade_submission(
    submission_id,
    *,
    score=None,
    is_correct=None,
    status="completed",
    max_score=None,
    normalized_response=None,
    grading_method="automatic",
    grading_version="",
    grader_version="",
):
    """Append a versioned grade and project its completed value onto legacy fields."""
    submission = SubmissionEvent.objects.select_for_update().select_related("attempt").get(pk=submission_id)
    try:
        status = GradeRecord.Status(status)
    except ValueError:
        raise ValidationError("无效的评分状态") from None
    grades = submission.grade_records.select_for_update()
    latest = grades.order_by("-version").first()
    effective = grades.filter(
        validity_status=GradeRecord.ValidityStatus.EFFECTIVE
    ).first()
    version = (latest.version + 1) if latest else 1
    now = timezone.now() if status == GradeRecord.Status.COMPLETED else None
    supersedes = latest if latest is not None else None
    validity_status = (
        GradeRecord.ValidityStatus.EFFECTIVE
        if status == GradeRecord.Status.COMPLETED
        else GradeRecord.ValidityStatus.CANDIDATE
    )
    if status == GradeRecord.Status.COMPLETED and effective is not None:
        effective.validity_status = GradeRecord.ValidityStatus.SUPERSEDED
        effective.save(update_fields=["validity_status"])
    record = GradeRecord.objects.create(
        submission=submission,
        version=version,
        status=status,
        validity_status=validity_status,
        score=score,
        is_correct=is_correct,
        grader_version=grader_version,
        grading_version=grading_version or grader_version,
        max_score=max_score,
        normalized_response=normalized_response,
        grading_method=grading_method,
        graded_at=now,
        supersedes_grade=supersedes,
    )
    submission.grading_status = status
    if status == GradeRecord.Status.COMPLETED:
        submission.score = score
        submission.is_correct = is_correct
        submission.save(update_fields=["grading_status", "score", "is_correct"])
        attempt = submission.attempt
        attempt.score = score
        attempt.is_correct = is_correct
        attempt.save(update_fields=["score", "is_correct"])
    else:
        submission.save(update_fields=["grading_status"])
    return record


@transaction.atomic
def void_grade_record(grade_record_id, *, reason, actor=None):
    """Void a grade without deleting the audit record or reviving an older score."""
    if not reason or not str(reason).strip():
        raise ValidationError("撤销评分必须说明原因")
    record = (
        GradeRecord.objects.select_for_update()
        .select_related("submission__attempt")
        .get(pk=grade_record_id)
    )
    if record.validity_status == GradeRecord.ValidityStatus.VOIDED:
        return record
    was_effective = record.validity_status == GradeRecord.ValidityStatus.EFFECTIVE
    record.validity_status = GradeRecord.ValidityStatus.VOIDED
    record.voided_at = timezone.now()
    record.voided_by = actor
    record.void_reason = str(reason).strip()
    record.save(
        update_fields=["validity_status", "voided_at", "voided_by", "void_reason"]
    )
    if was_effective:
        submission = record.submission
        submission.grading_status = SubmissionEvent.GradingStatus.REGRADE
        submission.score = None
        submission.is_correct = None
        submission.save(update_fields=["grading_status", "score", "is_correct"])
        attempt = submission.attempt
        attempt.score = None
        attempt.is_correct = None
        attempt.save(update_fields=["score", "is_correct"])
    return record


def submit_attempt(
    attempt_id,
    token,
    client_time=None,
    *,
    writer_lease_token=None,
    writer_client_instance_id=None,
    request_received_at=None,
):
    attempt, time_error = _submit_attempt_transaction(
        attempt_id,
        token,
        client_time,
        writer_lease_token,
        writer_client_instance_id,
        request_received_at,
    )
    if time_error:
        raise SubmissionValidationError(
            time_error,
            reason_code=(
                "task_terminated"
                if time_error == "班级任务已结束"
                else "time_expired"
            ),
        )
    return attempt

@transaction.atomic
def _mark_skipped_transaction(attempt_id):
    attempt = Attempt.objects.select_for_update().select_related(
        "session__version__experiment"
    ).get(pk=attempt_id)
    _validate_course_access(attempt.session)
    if attempt.session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    time_error = _validate_session_time(attempt.session, attempt)
    if time_error:
        return None, time_error
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能跳过")
    attempt.status = Attempt.Status.SKIPPED
    attempt.save(update_fields=["status"])
    return attempt, None


def mark_skipped(attempt_id):
    attempt, time_error = _mark_skipped_transaction(attempt_id)
    if time_error:
        raise ValidationError(time_error)
    return attempt

@transaction.atomic
def finish_session(session_id):
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    _validate_course_access(session)
    if session.status != ExperimentSession.Status.IN_PROGRESS: return session
    time_error = _validate_session_time(session)
    if time_error:
        return session
    now = timezone.now(); _close_previous(session, now)
    _close_open_attempts(
        session,
        status=Attempt.Status.UNANSWERED,
        close_reason="unanswered",
    )
    session.status = ExperimentSession.Status.COMPLETED
    session.completed_at = now
    session.terminal_at = now
    session.save(update_fields=["status", "completed_at", "terminal_at"])
    _append_session_event(
        session,
        event_type="session_completed",
        payload={"completion_reason": "student_finished", "terminal_at": now.isoformat()},
    )
    return session


@transaction.atomic
def invalidate_session(session_id, *, reason_code):
    session = ExperimentSession.objects.select_for_update().get(pk=session_id)
    if session.status == ExperimentSession.Status.INVALIDATED:
        event = AssessmentEvent.objects.get(
            session=session,
            event_type="session_invalidated",
        )
        if event.payload.get("reason_code") != reason_code:
            raise ValidationError("会话已使用不同原因作废")
        return session
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("只有进行中的会话可以作废")
    _close_open_attempts(
        session,
        status=Attempt.Status.UNANSWERED,
        close_reason="invalidated",
    )
    session.status = ExperimentSession.Status.INVALIDATED
    session.completed_at = timezone.now()
    session.terminal_at = session.completed_at
    session.save(update_fields=["status", "completed_at", "terminal_at"])
    _append_session_event(
        session,
        event_type="session_invalidated",
        payload={"reason_code": reason_code, "terminal_at": session.terminal_at.isoformat()},
    )
    return session


@transaction.atomic
def record_submission_rejection(*, attempt_id, submission_token, reason_code):
    try:
        token_value = uuid.UUID(str(submission_token))
    except (TypeError, ValueError):
        return None
    attempt = Attempt.objects.select_related(
        "session",
        "assignment__snapshot",
        "assignment__item_instance",
    ).get(pk=attempt_id)
    existing = AssessmentEvent.objects.filter(
        attempt=attempt,
        event_type="response_commit_rejected",
        correlation_id=token_value,
    ).first()
    if existing is not None:
        if existing.payload.get("reason_code") != reason_code:
            raise ValidationError("同一提交令牌的拒绝原因发生冲突")
        return existing
    return append_assessment_event(
        event_type="response_commit_rejected",
        event_version=1,
        event_domain="assessment",
        actor_source="server",
        session=attempt.session,
        assignment=attempt.assignment,
        attempt=attempt,
        question_snapshot=attempt.assignment.snapshot,
        item_instance=attempt.assignment.item_instance,
        correlation_id=token_value,
        payload={
            "submission_token": str(token_value),
            "reason_code": reason_code,
        },
        capture_mode="native",
        app_version="server",
    )
