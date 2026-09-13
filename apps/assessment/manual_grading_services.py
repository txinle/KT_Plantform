from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q

from apps.audit.models import AuditLog
from apps.experiments.models import ClassExperimentTask

from .models import GradeRecord, SubmissionEvent
from .services import grade_submission


MANUAL_BINARY_POLICY = "manual_binary_v1"


class ManualGradingError(ValueError):
    def __init__(self, message, *, code):
        self.code = code
        super().__init__(message)


def task_uses_manual_short_answer_grading(task):
    return (task.release_snapshot.configuration or {}).get(
        "short_answer_grading_policy"
    ) == MANUAL_BINARY_POLICY


def task_short_answer_snapshot_ids(task):
    snapshot_ids = set(
        SubmissionEvent.objects.filter(
            attempt__session__class_task=task,
            attempt__assignment__snapshot__question_type="short_answer",
        ).values_list("attempt__assignment__snapshot_id", flat=True)
    )
    for assignment_snapshot in task.participant_snapshots.values_list(
        "assignment_snapshot", flat=True
    ):
        for item in assignment_snapshot or []:
            snapshot_id = item.get("question_snapshot_id")
            if snapshot_id:
                snapshot_ids.add(snapshot_id)
    return snapshot_ids


def task_grading_summary(task):
    if not task_uses_manual_short_answer_grading(task):
        return {
            "enabled": False,
            "short_answer_questions": 0,
            "submitted": 0,
            "pending": 0,
            "graded": 0,
        }
    submissions = SubmissionEvent.objects.filter(
        attempt__session__class_task=task,
        attempt__assignment__snapshot__question_type="short_answer",
    )
    counts = submissions.aggregate(
        submitted=Count("pk"),
        pending=Count(
            "pk",
            filter=~Q(grading_status=SubmissionEvent.GradingStatus.COMPLETED),
        ),
        graded=Count(
            "pk",
            filter=Q(grading_status=SubmissionEvent.GradingStatus.COMPLETED),
        ),
    )
    return {
        "enabled": True,
        "short_answer_questions": len(task_short_answer_snapshot_ids(task)),
        **counts,
    }


def grading_submission_projection(submission):
    grades = list(
        submission.grade_records.select_related("grader").order_by("-version")
    )
    latest = grades[0] if grades else None
    effective = next(
        (
            grade
            for grade in grades
            if grade.validity_status == GradeRecord.ValidityStatus.EFFECTIVE
        ),
        None,
    )
    student = submission.attempt.session.student
    return {
        "submission_id": submission.pk,
        "student_id": student.pk,
        "student_number": student.student_number or "",
        "display_name": student.shown_name,
        "final_answer": submission.final_answer,
        "submitted_at": submission.committed_at,
        "grading_status": submission.grading_status,
        "latest_grade_version": latest.version if latest else 0,
        "current_is_correct": effective.is_correct if effective else None,
        "current_comment": effective.comment if effective else "",
        "graded_at": effective.graded_at if effective else None,
        "grader_name": (
            effective.grader.shown_name if effective and effective.grader else ""
        ),
    }


def task_grading_overview(task):
    snapshot_ids = task_short_answer_snapshot_ids(task)
    snapshots = task.release_snapshot.question_snapshots.filter(
        pk__in=snapshot_ids,
        question_type="short_answer",
    ).order_by("source_sequence", "pk")
    count_rows = {
        row["attempt__assignment__snapshot_id"]: row
        for row in SubmissionEvent.objects.filter(
            attempt__session__class_task=task,
            attempt__assignment__snapshot_id__in=snapshot_ids,
        )
        .values("attempt__assignment__snapshot_id")
        .annotate(
            submitted_count=Count("pk"),
            pending_count=Count(
                "pk",
                filter=~Q(grading_status=SubmissionEvent.GradingStatus.COMPLETED),
            ),
            graded_count=Count(
                "pk",
                filter=Q(grading_status=SubmissionEvent.GradingStatus.COMPLETED),
            ),
        )
    }
    questions = []
    for snapshot in snapshots:
        counts = count_rows.get(snapshot.pk, {})
        questions.append(
            {
                "question_snapshot_id": snapshot.pk,
                "code": snapshot.code,
                "text_summary": snapshot.text[:120],
                "submitted_count": counts.get("submitted_count", 0),
                "pending_count": counts.get("pending_count", 0),
                "graded_count": counts.get("graded_count", 0),
            }
        )
    return {
        "task": {
            "id": task.pk,
            "experiment_id": task.experiment_id,
            "experiment_name": task.experiment.name,
            "class_id": task.class_group_id,
            "class_name": str(task.class_group),
        },
        "totals": task_grading_summary(task),
        "questions": questions,
    }


@transaction.atomic
def grade_task_short_answer(
    *,
    task_id,
    submission_id,
    actor,
    expected_grade_version,
    is_correct,
    comment,
):
    task = (
        ClassExperimentTask.objects.select_for_update(of=("self",))
        .select_related("experiment__course", "release_snapshot")
        .get(pk=task_id)
    )
    course = task.experiment.course
    if not course.can_manage(
        actor,
        using=ClassExperimentTask.objects.db,
        lock_teacher_link=True,
    ):
        raise ManualGradingError(
            "作答不存在或不属于当前任务。",
            code="SUBMISSION_SCOPE_MISMATCH",
        )

    submission = (
        SubmissionEvent.objects.select_for_update()
        .select_related("attempt__assignment__snapshot", "attempt__session")
        .filter(pk=submission_id, attempt__session__class_task=task)
        .first()
    )
    if submission is None:
        raise ManualGradingError(
            "作答不存在或不属于当前任务。",
            code="SUBMISSION_SCOPE_MISMATCH",
        )
    snapshot = submission.attempt.assignment.snapshot
    if (
        snapshot.question_type != "short_answer"
        or (task.release_snapshot.configuration or {}).get(
            "short_answer_grading_policy"
        )
        != MANUAL_BINARY_POLICY
    ):
        raise ManualGradingError(
            "该作答不属于当前人工简答题评分规则。",
            code="NOT_MANUAL_SHORT_ANSWER",
        )

    grades = list(
        GradeRecord.objects.select_for_update()
        .filter(submission=submission)
        .order_by("-version")
    )
    latest = grades[0] if grades else None
    latest_version = latest.version if latest else 0
    if latest_version != expected_grade_version:
        raise ManualGradingError(
            "评分记录已经更新，请刷新后重试。",
            code="GRADE_VERSION_CONFLICT",
        )
    has_effective_grade = any(
        grade.validity_status == GradeRecord.ValidityStatus.EFFECTIVE
        for grade in grades
    )
    comment = str(comment or "").strip()
    if has_effective_grade and not comment:
        raise ManualGradingError(
            "修改已生效判定时必须填写原因。",
            code="INVALID_REGRADING_REASON",
        )

    record = grade_submission(
        submission.pk,
        score=Decimal("1") if is_correct else Decimal("0"),
        is_correct=bool(is_correct),
        status=GradeRecord.Status.COMPLETED,
        max_score=Decimal("1"),
        grading_method=MANUAL_BINARY_POLICY,
        grading_version=MANUAL_BINARY_POLICY,
        grader_version=MANUAL_BINARY_POLICY,
        actor=actor,
        comment=comment,
    )
    AuditLog.objects.create(
        actor=actor,
        action="class_task.short_answer_graded",
        object_type="SubmissionEvent",
        object_id=str(submission.pk),
        details={
            "task_id": task.pk,
            "question_snapshot_id": snapshot.pk,
            "grade_version": record.version,
            "expected_grade_version": expected_grade_version,
            "is_correct": bool(is_correct),
            "is_regrade": has_effective_grade,
        },
    )
    return record
