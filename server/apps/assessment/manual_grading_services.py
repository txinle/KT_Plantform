from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q

from apps.audit.models import AuditLog
from apps.experiments.models import ClassExperimentTask, ExperimentVersionParticipantSnapshot, QuestionSnapshot

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


def _short_answer_submissions(task_ids):
    return SubmissionEvent.objects.filter(
        attempt__session__class_task_id__in=task_ids,
        attempt__assignment__snapshot__question_type="short_answer",
    ).order_by()


def _assigned_question_ids(tasks, submitted_pairs):
    candidates = {task.pk: set() for task in tasks}
    for task_id, question_id in submitted_pairs:
        candidates[task_id].add(question_id)
    assignments = ExperimentVersionParticipantSnapshot.objects.filter(
        class_task_id__in=candidates
    ).values_list("class_task_id", "assignment_snapshot")
    for task_id, items in assignments:
        candidates[task_id].update(
            int(item["question_snapshot_id"]) for item in (items or []) if item.get("question_snapshot_id")
        )
    versions = dict(QuestionSnapshot.objects.filter(
        version_id__in=[task.release_snapshot_id for task in tasks],
        pk__in=set().union(*candidates.values()), question_type="short_answer",
    ).values_list("pk", "version_id"))
    return {
        task.pk: {qid for qid in candidates[task.pk] if versions.get(qid) == task.release_snapshot_id}
        for task in tasks
    }


def task_short_answer_snapshot_ids(task):
    submitted = _short_answer_submissions([task.pk]).values_list(
        "attempt__session__class_task_id", "attempt__assignment__snapshot_id",
    ).distinct()
    return _assigned_question_ids([task], submitted)[task.pk]


def _grading_data(tasks):
    summaries = {
        task.pk: {
            "enabled": task_uses_manual_short_answer_grading(task),
            "short_answer_questions": 0, "submitted": 0, "pending": 0, "graded": 0,
        }
        for task in tasks
    }
    manual = [task for task in tasks if summaries[task.pk]["enabled"]]
    rows = _short_answer_submissions([task.pk for task in manual]).values_list(
        "attempt__session__class_task_id", "attempt__assignment__snapshot_id",
    ).annotate(
        submitted=Count("pk"),
        graded=Count("pk", filter=Q(grading_status=SubmissionEvent.GradingStatus.COMPLETED)),
    )
    counts = {}
    for task_id, qid, submitted, graded in rows:
        counts[task_id, qid] = (submitted, graded)
        summary = summaries[task_id]
        summary["submitted"] += submitted
        summary["graded"] += graded
        summary["pending"] += submitted - graded
    assigned = _assigned_question_ids(manual, counts)
    for task_id, question_ids in assigned.items():
        summaries[task_id]["short_answer_questions"] = len(question_ids)
    return summaries, assigned, counts


def task_grading_summaries(tasks):
    summaries, _, _ = _grading_data(tasks)
    return summaries


def task_grading_summary(task):
    return task_grading_summaries([task])[task.pk]


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
    summaries, assigned, counts = _grading_data([task])
    snapshots = task.release_snapshot.question_snapshots.filter(
        pk__in=assigned.get(task.pk, ()),
    ).order_by("source_sequence", "pk")
    questions = []
    for snapshot in snapshots:
        submitted, graded = counts.get((task.pk, snapshot.pk), (0, 0))
        questions.append(
            {
                "question_snapshot_id": snapshot.pk,
                "code": snapshot.code,
                "text_summary": snapshot.text[:120],
                "submitted_count": submitted,
                "pending_count": submitted - graded,
                "graded_count": graded,
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
        "totals": summaries[task.pk],
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
        score=submission.attempt.assignment.snapshot.max_score.normalize() if is_correct else Decimal("0"),
        is_correct=bool(is_correct),
        status=GradeRecord.Status.COMPLETED,
        max_score=submission.attempt.assignment.snapshot.max_score.normalize(),
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
