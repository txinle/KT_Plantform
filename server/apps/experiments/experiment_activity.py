"""Read-only experiment activity for teacher lists and overviews."""

from django.db.models import Count, F, Prefetch, Q

from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.manual_grading_services import task_uses_manual_short_answer_grading
from apps.assessment.models import ExperimentSession, SubmissionEvent
from .models import (
    ClassExperimentTask, ExperimentVersionParticipantSnapshot, ScheduleRevision,
    TaskManualClosure,
)
from .publication_services import database_now


LIFECYCLE_LABELS = {
    'draft': '草稿', 'not_started': '未开始', 'started': '已开始', 'ended': '已结束',
}


def _lifecycle_summary(task_states, *, has_started):
    if not task_states:
        return {'lifecycle_status': 'draft', 'lifecycle_label': '草稿',
            'key_time_label': '开始时间', 'key_time': None}
    if all(state == 'scheduled' for _, state, _, _ in task_states) and not has_started:
        status, label = 'not_started', '开始时间'
        times = [schedule.opens_at for _, _, schedule, _ in task_states if schedule and schedule.opens_at]
        key_time = min(times) if times else None
    elif all(state not in ('scheduled', 'open') for _, state, _, _ in task_states):
        status, label = 'ended', '结束时间'
        times = [ended_at for _, _, _, ended_at in task_states if ended_at]
        key_time = max(times) if times else None
    else:
        status, label = 'started', '最近截止'
        times = [schedule.closes_at for _, state, schedule, _ in task_states
            if state in ('scheduled', 'open') and schedule and schedule.closes_at]
        key_time = min(times) if times else None
    return {'lifecycle_status': status, 'lifecycle_label': LIFECYCLE_LABELS[status],
        'key_time_label': label, 'key_time': key_time}


def experiment_activity_map(experiments, *, can_grade):
    experiments = {experiment.pk: experiment for experiment in experiments}
    result = {
        pk: {"pending_grading_count": 0, "grading_url": None, "schedules": [],
            'participant_count': 0, 'completed_count': 0, 'in_progress_count': 0}
        for pk in experiments
    }
    now = database_now()
    task_states = {pk: [] for pk in experiments}
    open_task_ids = []
    tasks = list(ClassExperimentTask.objects.filter(experiment_id__in=experiments)
        .select_related("class_group", "release_snapshot")
        .prefetch_related(Prefetch("schedule_revisions",
            queryset=ScheduleRevision.objects.order_by("-revision_no"),
            to_attr="latest_schedule_revisions"), Prefetch('manual_closures',
            queryset=TaskManualClosure.objects.order_by('-closed_at', '-pk'),
            to_attr='current_manual_closures'))
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
        schedule = DeadlineDecisionService.schedule_at(task, now)
        state = DeadlineDecisionService.get_task_state(task, at=now, schedule=schedule)
        if state == 'open':
            open_task_ids.append(task.pk)
        ended_at = {
            'closed': schedule.closes_at if schedule else None,
            'manual_closed': DeadlineDecisionService.manual_close_at(task, now),
            'cancelled': task.cancelled_at, 'invalidated': task.invalidated_at,
        }.get(state)
        task_states[task.experiment_id].append((task.pk, state, schedule, ended_at))
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
    session_started = Q(sessions__started_at__isnull=False,
        sessions__class_task_id=F('class_task_id')) & ~Q(
        sessions__status=ExperimentSession.Status.PREPARED)
    active = (session_started & Q(class_task_id__in=open_task_ids,
        sessions__status__in=[ExperimentSession.Status.IN_PROGRESS, ExperimentSession.Status.SUBMITTING])
        & (Q(sessions__deadline_at__isnull=True) | Q(sessions__deadline_at__gt=now))
        & (Q(sessions__authoritative_terminal_at__isnull=True) | Q(sessions__authoritative_terminal_at__gt=now)))
    participants = (ExperimentVersionParticipantSnapshot.objects.filter(
        class_task__experiment_id__in=experiments)
        .order_by().values('class_task__experiment_id', 'student_id').annotate(
            assigned=Count('pk', distinct=True),
            completed=Count('pk', distinct=True, filter=session_started & Q(
                sessions__status=ExperimentSession.Status.COMPLETED)),
            in_progress=Count('pk', distinct=True, filter=active),
            started=Count('pk', distinct=True, filter=session_started)))
    started_experiments = set()
    for participant in participants:
        experiment_id = participant['class_task__experiment_id']
        row = result[experiment_id]
        row['participant_count'] += 1
        if participant['completed'] == participant['assigned']:
            row['completed_count'] += 1
        elif participant['in_progress']:
            row['in_progress_count'] += 1
        if participant['started']:
            started_experiments.add(experiment_id)
    for experiment_id, row in result.items():
        row.update(_lifecycle_summary(task_states[experiment_id],
            has_started=experiment_id in started_experiments))
    return result
