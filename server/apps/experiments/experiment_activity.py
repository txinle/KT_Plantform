"""Read-only experiment activity for teacher lists and overviews."""

from django.db.models import Count, Prefetch

from apps.assessment.manual_grading_services import task_uses_manual_short_answer_grading
from apps.assessment.models import SubmissionEvent
from .models import ClassExperimentTask, ScheduleRevision


def experiment_activity_map(experiments, *, can_grade):
    experiments = {experiment.pk: experiment for experiment in experiments}
    result = {
        pk: {"pending_grading_count": 0, "grading_url": None, "schedules": []}
        for pk in experiments
    }
    tasks = list(ClassExperimentTask.objects.filter(experiment_id__in=experiments)
        .select_related("class_group", "release_snapshot")
        .prefetch_related(Prefetch("schedule_revisions",
            queryset=ScheduleRevision.objects.order_by("-revision_no"),
            to_attr="activity_schedules"))
        .order_by("experiment_id", "class_group__name", "pk"))
    manual_task_ids = [task.pk for task in tasks if task_uses_manual_short_answer_grading(task)]
    pending = dict(SubmissionEvent.objects.filter(
        attempt__session__class_task_id__in=manual_task_ids,
        attempt__assignment__snapshot__question_type="short_answer",
    ).exclude(grading_status=SubmissionEvent.GradingStatus.COMPLETED)
        .values("attempt__session__class_task_id").annotate(count=Count("pk"))
        .values_list("attempt__session__class_task_id", "count"))
    for task in tasks:
        row = result[task.experiment_id]
        count = pending.get(task.pk, 0)
        had_pending = row["pending_grading_count"] > 0
        row["pending_grading_count"] += count
        if can_grade and task.pk in manual_task_ids and (
            row["grading_url"] is None or (count > 0 and not had_pending)
        ):
            course_id = experiments[task.experiment_id].course_id
            row["grading_url"] = f"/teacher/courses/{course_id}/experiments/{task.experiment_id}/class-tasks/{task.pk}/grading/"
        schedule = next(iter(task.activity_schedules), None)
        configuration = task.release_snapshot.configuration
        total_seconds = (schedule.total_time_limit_seconds if schedule else None)
        item_seconds = (schedule.per_question_time_limit_seconds if schedule else None)
        row["schedules"].append({
            "task_id": task.pk,
            "class_name": str(task.class_group),
            "opens_at": schedule.opens_at if schedule else None,
            "closes_at": schedule.closes_at if schedule else None,
            "total_time_limit_minutes": (
                (total_seconds or int(configuration.get("total_time_limit_minutes") or 0) * 60) // 60
                if configuration.get("time_mode") == "total" else None
            ),
            "per_question_time_limit_seconds": (
                item_seconds or int(configuration.get("per_question_time_limit_seconds") or 0)
                if configuration.get("time_mode") == "per_question" else None
            ),
        })
    return result
