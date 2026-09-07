"""Authoritative release decisions for every student feedback projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.experiments.models import ClassExperimentTask, Experiment

from .deadline_services import DeadlineDecisionService
from .models import GradeRecord, SubmissionEvent


LEGACY_FEEDBACK_RELEASE_POLICY = "legacy"
INVALID_FEEDBACK_RELEASE_POLICY = "invalid"


class FeedbackReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class InitialGradingResult:
    status: str
    is_correct: bool | None
    score: Decimal | None
    max_score: Decimal | None
    method: str


@dataclass(frozen=True)
class FeedbackAvailability:
    feedback_mode: str
    release_policy: str
    released: bool
    reason: str
    session_status: str | None
    effective_grade: object | None = None

    @property
    def grade_ready(self) -> bool:
        return self.effective_grade is not None

    @property
    def phase(self) -> str:
        if self.feedback_mode == Experiment.Feedback.NONE:
            return "disabled"
        if not self.released:
            return "not_released"
        if not self.grade_ready:
            return "pending_grading"
        return "available"

    @property
    def allows_correctness(self) -> bool:
        return (
            self.released
            and self.grade_ready
            and self.feedback_mode
            in {Experiment.Feedback.CORRECTNESS, Experiment.Feedback.FULL}
        )

    @property
    def allows_full(self) -> bool:
        return (
            self.allows_correctness
            and self.feedback_mode == Experiment.Feedback.FULL
        )


def feedback_release_policy_for_configuration(configuration) -> str:
    configuration = configuration or {}
    value = configuration.get("feedback_release_policy")
    if value is None:
        return LEGACY_FEEDBACK_RELEASE_POLICY
    if value in Experiment.FeedbackReleasePolicy.values:
        return value
    return INVALID_FEEDBACK_RELEASE_POLICY


def _effective_grade_for_attempt(attempt):
    if attempt is None:
        return None
    return (
        GradeRecord.objects.filter(
            submission__attempt_id=attempt.pk,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        )
        .select_related("submission")
        .first()
    )


def feedback_availability(
    task,
    session,
    *,
    attempt=None,
    effective_grade=None,
    load_effective_grade=False,
    at=None,
) -> FeedbackAvailability:
    configuration = task.release_snapshot.configuration or {}
    feedback_mode = configuration.get("feedback_mode", Experiment.Feedback.NONE)
    release_policy = feedback_release_policy_for_configuration(configuration)
    session_status = getattr(session, "status", None)
    if feedback_mode == Experiment.Feedback.NONE:
        return FeedbackAvailability(
            feedback_mode=feedback_mode,
            release_policy=release_policy,
            released=False,
            reason="feedback_disabled",
            session_status=session_status,
        )

    at = at or timezone.now()
    if release_policy == LEGACY_FEEDBACK_RELEASE_POLICY:
        released, reason = True, "legacy_frozen_policy"
    elif release_policy == Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION:
        released = session_status == "completed"
        reason = "personal_submission_required" if not released else "personal_submission"
    elif release_policy == Experiment.FeedbackReleasePolicy.AFTER_ASSIGNMENT_DEADLINE:
        released = DeadlineDecisionService.get_task_state(task, at=at) == "closed"
        reason = "assignment_deadline_required" if not released else "assignment_deadline"
    elif release_policy == Experiment.FeedbackReleasePolicy.MANUAL_RELEASE:
        released = task.feedback_released_at is not None
        reason = "manual_release_required" if not released else "manual_release"
    else:
        released, reason = False, "invalid_frozen_policy"

    if released and attempt is not None and load_effective_grade:
        effective_grade = _effective_grade_for_attempt(attempt)
    return FeedbackAvailability(
        feedback_mode=feedback_mode,
        release_policy=release_policy,
        released=released,
        reason=reason,
        session_status=session_status,
        effective_grade=effective_grade,
    )


def frozen_feedback_payload(attempt):
    """Project only feedback that is both released and authoritatively graded."""
    from .models import Attempt

    attempt = (
        Attempt.objects.select_related(
            "session__class_task__release_snapshot",
            "assignment__snapshot",
        )
        .get(pk=attempt.pk)
    )
    task = attempt.session.class_task
    if task is None:
        # Pre-class-task legacy sessions retain their historical feedback path.
        configuration = attempt.session.version.configuration or {}
        feedback_mode = configuration.get(
            "feedback_mode",
            attempt.session.version.experiment.feedback_mode,
        )
        availability = FeedbackAvailability(
            feedback_mode=feedback_mode,
            release_policy=LEGACY_FEEDBACK_RELEASE_POLICY,
            released=True,
            reason="legacy_session",
            session_status=attempt.session.status,
            effective_grade=_effective_grade_for_attempt(attempt),
        )
    else:
        availability = feedback_availability(
            task,
            attempt.session,
            attempt=attempt,
            load_effective_grade=True,
        )
    result = {
        "feedback_mode": availability.feedback_mode,
        "feedback_phase": availability.phase,
    }
    if availability.allows_correctness:
        result["is_correct"] = availability.effective_grade.is_correct
    if availability.allows_full:
        result.update(
            {
                "correct_answer": attempt.assignment.snapshot.correct_answer,
                "explanation": attempt.assignment.snapshot.explanation,
            }
        )
    return result


def prepare_feedback_exposure(parsed):
    """Reject client exposure claims that do not match the server projection."""
    attempt = parsed.attempt
    task = getattr(parsed.session, "class_task", None)
    if attempt is None or task is None:
        raise FeedbackReleaseError("反馈曝光必须关联正式班级作答。")
    availability = feedback_availability(
        task,
        parsed.session,
        attempt=attempt,
        load_effective_grade=True,
    )
    if not availability.released:
        raise FeedbackReleaseError("当前反馈尚不可见。")
    payload = dict(parsed.payload)
    if payload["feedback_mode"] != availability.feedback_mode:
        raise FeedbackReleaseError("反馈模式与冻结版本不一致。")
    flags = tuple(
        payload[key]
        for key in (
            "included_correctness",
            "included_correct_answer",
            "included_explanation",
        )
    )
    stage = payload["stage"]
    if stage == "detail_entered":
        expected = (availability.allows_correctness, False, False)
        if flags != expected:
            raise FeedbackReleaseError("详情进入事件未如实记录已呈现内容。")
    elif stage == "answer_presented":
        if not availability.allows_full or flags != (True, True, False):
            raise FeedbackReleaseError("正确答案尚不可呈现。")
    elif stage == "explanation_presented":
        if not availability.allows_full or flags != (True, True, True):
            raise FeedbackReleaseError("解析尚不可呈现。")
    else:
        allowed_flags = {(False, False, False)}
        if availability.allows_correctness:
            allowed_flags.add((True, False, False))
        if availability.allows_full:
            allowed_flags.update({(True, True, False), (True, True, True)})
        if flags not in allowed_flags:
            raise FeedbackReleaseError("详情离开事件包含未获授权的反馈内容。")

    from .models import AssessmentEvent

    prior_payloads = list(
        AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="feedback_exposure",
            payload__operation_id=payload["operation_id"],
        ).values_list("payload", flat=True)
    )
    prior_stages = {item.get("stage") for item in prior_payloads}
    if stage == "answer_presented" and "detail_entered" not in prior_stages:
        raise FeedbackReleaseError("正确答案曝光必须从详情进入事件开始。")
    if stage == "explanation_presented" and "answer_presented" not in prior_stages:
        raise FeedbackReleaseError("解析曝光必须在正确答案曝光之后。")
    if stage == "detail_left":
        if "detail_entered" not in prior_stages:
            raise FeedbackReleaseError("详情离开事件缺少对应的进入事件。")
        prior_flags = tuple(
            any(item.get(key, False) for item in prior_payloads)
            for key in (
                "included_correctness",
                "included_correct_answer",
                "included_explanation",
            )
        )
        if flags != prior_flags:
            raise FeedbackReleaseError("详情离开事件与已记录的实际曝光不一致。")
    expected_answer_event_id = (
        str(attempt.final_response_event_id)
        if attempt.final_response_event_id is not None
        else None
    )
    expected_snapshot_id = (
        str(attempt.final_response_snapshot_id)
        if attempt.final_response_snapshot_id is not None
        else None
    )
    if payload["answer_event_id"] != expected_answer_event_id:
        raise FeedbackReleaseError("反馈曝光引用的答案事件不一致。")
    if payload["snapshot_id"] != expected_snapshot_id:
        raise FeedbackReleaseError("反馈曝光引用的答案快照不一致。")
    return payload


def initial_grading_result(attempt, correct, score) -> InitialGradingResult:
    """Keep manual capture-v2 short answers pending without treating them as zero."""
    from .capture_v2_services import uses_capture_v2

    if (
        uses_capture_v2(attempt.session)
        and attempt.assignment.snapshot.question_type == "short_answer"
    ):
        return InitialGradingResult(
            status=SubmissionEvent.GradingStatus.PENDING,
            is_correct=None,
            score=None,
            max_score=None,
            method="manual_short_answer_v1",
        )
    return InitialGradingResult(
        status=SubmissionEvent.GradingStatus.COMPLETED,
        is_correct=correct,
        score=score,
        max_score=Decimal("1"),
        method="automatic",
    )


@transaction.atomic
def release_task_feedback(task, actor, *, reason=""):
    """Make a manual-feedback task available and retain an audit fact."""
    task = (
        ClassExperimentTask.objects.select_for_update()
        .select_related("release_snapshot")
        .get(pk=task.pk)
    )
    policy = feedback_release_policy_for_configuration(
        task.release_snapshot.configuration
    )
    if policy != Experiment.FeedbackReleasePolicy.MANUAL_RELEASE:
        raise FeedbackReleaseError("该班级任务未配置为人工发布反馈。")
    if task.feedback_released_at is not None:
        return task
    if actor is None or actor.pk is None:
        raise FeedbackReleaseError("人工发布反馈必须记录教师。")

    task.feedback_released_at = timezone.now()
    task.feedback_released_by = actor
    from apps.experiments.services import _class_experiment_task_terminal_update_scope

    with _class_experiment_task_terminal_update_scope():
        task.save(update_fields=["feedback_released_at", "feedback_released_by"])
    AuditLog.objects.create(
        actor=actor,
        action="class_task.feedback_release",
        object_type=task.__class__.__name__,
        object_id=str(task.pk),
        details={
            "class_group_id": task.class_group_id,
            "release_snapshot_id": task.release_snapshot_id,
            "release_policy": policy,
            "reason": str(reason or "").strip(),
        },
    )
    return task
