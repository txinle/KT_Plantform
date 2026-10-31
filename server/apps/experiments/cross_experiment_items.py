from dataclasses import dataclass
from collections import defaultdict

from django.db.models import Prefetch
from django.utils import timezone

from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.models import (
    StudentQuestionAssignment,
    SubmissionEvent,
)

from .models import ExperimentVersionParticipantSnapshot, ScheduleRevision, TaskManualClosure


@dataclass(frozen=True)
class StudentItemContext:
    completed_question_ids: frozenset[int]
    assigned_question_ids: frozenset[int]
    active_frozen_question_ids: frozenset[int]
    unresolved_task_ids: tuple[int, ...]


def student_item_context(experiment, student, *, at=None):
    """Read one student's formal history and persisted task assignments."""
    return student_item_contexts(experiment, [student], at=at).get(
        student.pk,
        StudentItemContext(
            completed_question_ids=frozenset(),
            assigned_question_ids=frozenset(),
            active_frozen_question_ids=frozenset(),
            unresolved_task_ids=(),
        ),
    )


def student_item_contexts(experiment, students, *, at=None):
    """Read the same cross-experiment context for many students in bounded queries."""
    student_ids = {student.pk for student in students}
    empty = StudentItemContext(
        completed_question_ids=frozenset(),
        assigned_question_ids=frozenset(),
        active_frozen_question_ids=frozenset(),
        unresolved_task_ids=(),
    )
    if not student_ids or experiment.course_id is None:
        return {student_id: empty for student_id in student_ids}

    completed_by_student = defaultdict(set)
    for student_id, question_id in SubmissionEvent.objects.filter(
        attempt__session__student_id__in=student_ids,
        attempt__session__version__experiment__course_id=experiment.course_id,
    ).values_list(
        "attempt__session__student_id",
        "attempt__assignment__snapshot__source_question_id",
    ):
        completed_by_student[student_id].add(question_id)

    assigned_by_student = defaultdict(set)
    runtime_by_student_task = defaultdict(set)
    for student_id, task_id, question_id in StudentQuestionAssignment.objects.filter(
        session__student_id__in=student_ids,
        session__version__experiment__course_id=experiment.course_id,
    ).exclude(
        session__version__experiment_id=experiment.pk,
    ).values_list(
        "session__student_id",
        "session__class_task_id",
        "snapshot__source_question_id",
    ):
        assigned_by_student[student_id].add(question_id)
        if task_id is not None:
            runtime_by_student_task[(student_id, task_id)].add(question_id)

    participants = ExperimentVersionParticipantSnapshot.objects.filter(
        student_id__in=student_ids,
        class_task__experiment__course_id=experiment.course_id,
    ).exclude(
        class_task__experiment_id=experiment.pk,
    ).select_related(
        "class_task",
    ).prefetch_related(
        Prefetch(
            "class_task__schedule_revisions",
            queryset=ScheduleRevision.objects.order_by("-revision_no"),
            to_attr="latest_schedule_revisions",
        ),
        Prefetch(
            "class_task__manual_closures",
            queryset=TaskManualClosure.objects.order_by("-closed_at", "-pk"),
            to_attr="current_manual_closures",
        ),
    ).order_by("class_task_id")
    at = at or timezone.now()
    active_by_student = defaultdict(set)
    unresolved_by_student = defaultdict(set)
    task_states = {}
    for participant in participants:
        task = participant.class_task
        rows = participant.assignment_snapshot
        if task.pk not in task_states:
            task_states[task.pk] = DeadlineDecisionService.get_task_state(task, at=at)
        task_state = task_states[task.pk]
        expected_count = len(rows) if isinstance(rows, list) and rows else None
        if isinstance(rows, list) and rows:
            source_ids = [
                row.get("source_question_id")
                for row in rows
                if (
                    isinstance(row, dict)
                    and type(row.get("source_question_id")) is int
                    and row["source_question_id"] > 0
                )
            ]
            if len(source_ids) != len(rows):
                source_ids = []
        else:
            source_ids = []
        if not source_ids:
            source_ids = runtime_by_student_task[(participant.student_id, task.pk)]
            if not source_ids or (expected_count is not None and len(source_ids) < expected_count):
                unresolved_by_student[participant.student_id].add(task.pk)
                continue
        assigned_by_student[participant.student_id].update(source_ids)
        if task_state not in {"scheduled", "open"}:
            continue
        active_by_student[participant.student_id].update(source_ids)

    return {
        student_id: StudentItemContext(
            completed_question_ids=frozenset(completed_by_student[student_id]),
            assigned_question_ids=frozenset(assigned_by_student[student_id]),
            active_frozen_question_ids=frozenset(active_by_student[student_id]),
            unresolved_task_ids=tuple(sorted(unresolved_by_student[student_id])),
        )
        for student_id in student_ids
    }
