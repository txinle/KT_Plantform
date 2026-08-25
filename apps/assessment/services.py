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
from .models import Attempt, DraftSaveEvent, ExperimentSession, QuestionVisitEvent, StudentQuestionAssignment, SubmissionEvent

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
        session.attempts.exclude(status=Attempt.Status.SUBMITTED).filter(
            status__in=[Attempt.Status.UNVISITED, Attempt.Status.VISITED, Attempt.Status.DRAFT, Attempt.Status.SKIPPED]
        ).update(status=Attempt.Status.UNANSWERED, is_correct=None)
        session.status = ExperimentSession.Status.EXPIRED
        session.completed_at = now
        session.save(update_fields=["status", "completed_at"])
        raise ValidationError("实验总时间已结束")
    if attempt and session.version.configuration.get("time_mode") == "per_question":
        limit = session.version.configuration.get("per_question_time_limit_seconds")
        elapsed = attempt.active_seconds
        if attempt.last_opened_at: elapsed += max(0, int((now - attempt.last_opened_at).total_seconds()))
        if limit and elapsed >= int(limit):
            attempt.status = Attempt.Status.TIMED_OUT; attempt.is_correct = None
            attempt.save(update_fields=["status", "is_correct"])
            raise ValidationError("本题作答时间已结束")

@transaction.atomic
def record_visit(session_id, assignment_id, entry_reason="navigation_click", client_time=None):
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    if session.status != ExperimentSession.Status.IN_PROGRESS: raise ValidationError("当前会话不可继续作答")
    _validate_session_time(session)
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
    return attempt

@transaction.atomic
def save_draft(attempt_id, answer, client_time=None):
    attempt = Attempt.objects.select_for_update().select_related(
        "session__version__experiment"
    ).get(pk=attempt_id)
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能修改")
    if attempt.session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    _validate_session_time(attempt.session, attempt)
    attempt.draft_answer = answer; attempt.draft_saved_at = timezone.now(); attempt.status = Attempt.Status.DRAFT
    attempt.save(update_fields=["draft_answer", "draft_saved_at", "status"])
    digest = hashlib.sha256(repr(answer).encode()).hexdigest()
    DraftSaveEvent.objects.create(attempt=attempt, answer_hash=digest, client_occurred_at=client_time)
    return attempt

@transaction.atomic
def submit_attempt(attempt_id, token, client_time=None):
    attempt = Attempt.objects.select_for_update().select_related("session", "assignment__snapshot").get(pk=attempt_id)
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=attempt.session_id)
    _validate_course_access(session)
    if attempt.status == Attempt.Status.SUBMITTED:
        if str(attempt.submission_token) == str(token):
            return attempt
        raise ValidationError("该题已经提交")
    if attempt.status in {Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能提交")
    _validate_session_time(session, attempt)
    if attempt.draft_answer in (None, "", []): raise ValidationError("请先填写答案")
    try: token_value = uuid.UUID(str(token))
    except ValueError: raise ValidationError("提交令牌无效") from None
    snapshot = attempt.assignment.snapshot
    if snapshot.question_type == "numeric":
        try:
            normalize_finite_decimal(attempt.draft_answer)
        except ValueError:
            raise ValidationError("数值题答案必须是有效数字") from None
    correct, score = score_payload(
        snapshot.question_type, snapshot.correct_answer, attempt.draft_answer,
        accepted_answers=snapshot.accepted_answers,
        absolute_tolerance=snapshot.absolute_tolerance,
        relative_tolerance=snapshot.relative_tolerance,
    )
    now = timezone.now(); session.submission_count += 1
    attempt.status = Attempt.Status.SUBMITTED; attempt.final_answer = attempt.draft_answer
    attempt.submitted_at = now; attempt.submission_sequence_index = session.submission_count
    attempt.is_correct = correct; attempt.score = score; attempt.submission_token = token_value
    if attempt.first_opened_at: attempt.first_to_submit_duration_ms = int((now - attempt.first_opened_at).total_seconds() * 1000)
    if attempt.last_opened_at: attempt.last_visit_to_submission_ms = int((now - attempt.last_opened_at).total_seconds() * 1000)
    attempt.save()
    session.save(update_fields=["submission_count", "last_activity_at"])
    SubmissionEvent.objects.create(
        attempt=attempt, submission_token=token_value, final_answer=attempt.final_answer,
        is_correct=correct, score=score, client_clicked_at=client_time,
        server_received_at=now, committed_at=now,
    )
    return attempt

@transaction.atomic
def mark_skipped(attempt_id):
    attempt = Attempt.objects.select_for_update().select_related(
        "session__version__experiment"
    ).get(pk=attempt_id)
    _validate_course_access(attempt.session)
    if attempt.session.status != ExperimentSession.Status.IN_PROGRESS:
        raise ValidationError("当前会话不可继续作答")
    if attempt.status in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
        raise ValidationError("该题当前状态不能跳过")
    attempt.status = Attempt.Status.SKIPPED
    attempt.save(update_fields=["status"])
    return attempt

@transaction.atomic
def finish_session(session_id):
    session = ExperimentSession.objects.select_for_update().select_related(
        "version__experiment"
    ).get(pk=session_id)
    _validate_course_access(session)
    if session.status != ExperimentSession.Status.IN_PROGRESS: return session
    now = timezone.now(); _close_previous(session, now)
    session.attempts.filter(status__in=[Attempt.Status.UNVISITED, Attempt.Status.VISITED, Attempt.Status.DRAFT]).update(status=Attempt.Status.UNANSWERED, is_correct=None)
    session.status = ExperimentSession.Status.COMPLETED; session.completed_at = now; session.save(update_fields=["status", "completed_at"])
    return session
