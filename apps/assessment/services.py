from __future__ import annotations
import hashlib
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from apps.experiments.models import Experiment, ExperimentStudent, ExperimentVersion
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
    canonical_choice_response_state,
    create_response_snapshot,
    response_snapshot_content,
)
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
        event_version=1,
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


def _close_open_attempts(session, *, status, close_reason):
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
        attempt.save(update_fields=["status", "is_correct"])
        _append_attempt_closed(attempt, close_reason=close_reason)
    return attempts

@transaction.atomic
def get_or_create_session(experiment, student):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.course_id:
        from apps.accounts.models import Course, CourseEnrollment

        course = Course.objects.select_for_update().get(pk=experiment.course_id)
        if course.status != Course.Status.ACTIVE:
            raise ValidationError("课程已归档，不能开始或继续实验")

        if not CourseEnrollment.objects.select_for_update().filter(
            course_id=experiment.course_id,
            student=student,
            is_active=True,
        ).exists():
            raise ValidationError("你没有加入该实验所属课程")
    enrollment = ExperimentStudent.objects.filter(experiment=experiment, student=student).first()
    if not enrollment: raise ValidationError("你没有参加该实验的权限")
    if experiment.status != Experiment.Status.PUBLISHED: raise ValidationError("实验尚未开放")
    version = (
        ExperimentVersion.objects.select_for_update()
        .filter(experiment=experiment)
        .order_by("-version_number")
        .first()
    )
    sampling_seed, ordering_seed, algorithm_version = derive_session_seeds(version, student.id)
    session, created = ExperimentSession.objects.get_or_create(
        version=version,
        student=student,
        defaults={
            "class_snapshot_name": enrollment.class_snapshot_name,
            "sampling_seed": sampling_seed,
            "ordering_seed": ordering_seed,
            "sampling_algorithm_version": algorithm_version,
        },
    )
    if created:
        if version.configuration.get("time_mode") == "total":
            session.deadline_at = timezone.now() + timedelta(minutes=int(version.configuration["total_time_limit_minutes"]))
            session.save(update_fields=["deadline_at"])
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
            payload={},
        )
    return session

def _close_previous(session, now):
    previous = session.current_assignment
    if not previous: return
    attempt = Attempt.objects.select_for_update().get(assignment=previous)
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}: return
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


def _validate_session_time(session, attempt=None):
    _validate_course_access(session)
    now = timezone.now()
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    if session.deadline_at and now >= session.deadline_at:
        _close_open_attempts(
            session,
            status=Attempt.Status.UNANSWERED,
            close_reason="unanswered",
        )
        session.status = ExperimentSession.Status.EXPIRED
        session.completed_at = now
        session.save(update_fields=["status", "completed_at"])
        _append_session_event(
            session,
            event_type="session_expired",
            payload={"expiry_reason": "total_time_limit"},
        )
        return "实验总时间已结束"
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
def _record_visit_transaction(session_id, assignment_id, entry_reason="navigation_click", client_time=None):
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    if session.status != ExperimentSession.Status.IN_PROGRESS: raise ValidationError("当前会话不可继续作答")
    time_error = _validate_session_time(session)
    if time_error:
        return None, time_error
    assignment = StudentQuestionAssignment.objects.select_related("snapshot").get(pk=assignment_id, session=session)
    now = timezone.now(); from_assignment = session.current_assignment
    _close_previous(session, now)
    attempt = Attempt.objects.select_for_update().get(assignment=assignment)
    if attempt.status not in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        if attempt.first_opened_at is None:
            session.first_open_count += 1
            attempt.first_open_sequence_index = session.first_open_count
            attempt.first_opened_at = now
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
        event_version=1,
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


def record_visit(session_id, assignment_id, entry_reason="navigation_click", client_time=None):
    attempt, time_error = _record_visit_transaction(
        session_id,
        assignment_id,
        entry_reason,
        client_time,
    )
    if time_error:
        raise ValidationError(time_error)
    return attempt

@transaction.atomic
def _save_draft_transaction(attempt_id, answer, client_time=None):
    attempt = Attempt.objects.select_for_update().select_related(
        "session__version__experiment",
        "assignment__snapshot",
    ).get(pk=attempt_id)
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能修改")
    if attempt.session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    time_error = _validate_session_time(attempt.session, attempt)
    if time_error:
        return None, time_error
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


def save_draft(attempt_id, answer, client_time=None):
    attempt, time_error = _save_draft_transaction(attempt_id, answer, client_time)
    if time_error:
        raise ValidationError(time_error)
    return attempt

@transaction.atomic
def _submit_attempt_transaction(attempt_id, token, client_time=None):
    attempt = Attempt.objects.select_for_update().select_related("session", "assignment__snapshot").get(pk=attempt_id)
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=attempt.session_id)
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
    if attempt.status in {Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise SubmissionValidationError(
            "该题当前状态不能提交",
            reason_code=(
                "time_expired"
                if attempt.status == Attempt.Status.TIMED_OUT
                else "session_closed"
            ),
        )
    time_error = _validate_session_time(session, attempt)
    if time_error:
        return None, time_error
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
        is_correct=correct,
        score=score,
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
            "grading_status": submission.grading_status,
            "score": float(score),
            "is_correct": correct,
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
    latest = submission.grade_records.order_by("-version").first()
    version = (latest.version + 1) if latest else 1
    now = timezone.now() if status == GradeRecord.Status.COMPLETED else None
    supersedes = latest if status == GradeRecord.Status.REGRADE else None
    record = GradeRecord.objects.create(
        submission=submission,
        version=version,
        status=status,
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


def submit_attempt(attempt_id, token, client_time=None):
    attempt, time_error = _submit_attempt_transaction(attempt_id, token, client_time)
    if time_error:
        raise SubmissionValidationError(time_error, reason_code="time_expired")
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
    session.status = ExperimentSession.Status.COMPLETED; session.completed_at = now; session.save(update_fields=["status", "completed_at"])
    _append_session_event(
        session,
        event_type="session_completed",
        payload={"completion_reason": "student_finished"},
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
    session.save(update_fields=["status", "completed_at"])
    _append_session_event(
        session,
        event_type="session_invalidated",
        payload={"reason_code": reason_code},
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
