from dataclasses import dataclass
from collections import defaultdict

from django.db.models import Prefetch
from django.utils import timezone

from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.models import ExperimentSession, SubmissionEvent

from .models import ExperimentVersionParticipantSnapshot, ScheduleRevision


@dataclass(frozen=True)
class StudentItemContext:
    completed_question_ids: frozenset[int]
    active_frozen_question_ids: frozenset[int]
    unresolved_task_ids: tuple[int, ...]


def student_item_context(experiment, student, *, at=None):
    """Read formal history and other active frozen assignments without persisting a ledger."""
    if experiment.course_id is None:
        return StudentItemContext(frozenset(), frozenset(), ())

    completed = frozenset(
        SubmissionEvent.objects.filter(
            attempt__session__student=student,
            attempt__session__version__experiment__course_id=experiment.course_id,
        ).values_list(
            "attempt__assignment__snapshot__source_question_id",
            flat=True,
        )
    )
    at = at or timezone.now()
    active = set()
    unresolved = []
    participants = (
        ExperimentVersionParticipantSnapshot.objects.filter(
            student=student,
            class_task__experiment__course_id=experiment.course_id,
        )
        .exclude(class_task__experiment_id=experiment.pk)
        .select_related("class_task")
        .order_by("class_task_id")
    )
    for participant in participants:
        task = participant.class_task
        if DeadlineDecisionService.get_task_state(task, at=at) not in {"scheduled", "open"}:
            continue
        rows = participant.assignment_snapshot
        source_ids = []
        if isinstance(rows, list) and rows:
            source_ids = [
                row.get("source_question_id")
                for row in rows
                if isinstance(row, dict) and row.get("source_question_id") is not None
            ]
            if len(source_ids) != len(rows):
                unresolved.append(task.pk)
                continue
        else:
            session = participant.sessions.filter(class_task=task).first()
            if session is None:
                unresolved.append(task.pk)
                continue
            source_ids = list(
                session.assignments.values_list("snapshot__source_question_id", flat=True)
            )
            if not source_ids:
                unresolved.append(task.pk)
                continue
        active.update(source_ids)
    return StudentItemContext(
        completed_question_ids=completed,
        active_frozen_question_ids=frozenset(active),
        unresolved_task_ids=tuple(sorted(set(unresolved))),
    )


def student_item_contexts(experiment, students, *, at=None):
    """Read the same cross-experiment context for many students in bounded queries."""
    student_ids = {student.pk for student in students}
    empty = StudentItemContext(frozenset(), frozenset(), ())
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
            "sessions",
            queryset=ExperimentSession.objects.prefetch_related(
                "assignments__snapshot"
            ).order_by("pk"),
            to_attr="context_sessions",
        ),
    ).order_by("class_task_id")
    at = at or timezone.now()
    active_by_student = defaultdict(set)
    unresolved_by_student = defaultdict(set)
    for participant in participants:
        task = participant.class_task
        if DeadlineDecisionService.get_task_state(task, at=at) not in {
            "scheduled",
            "open",
        }:
            continue
        rows = participant.assignment_snapshot
        if isinstance(rows, list) and rows:
            source_ids = [
                row.get("source_question_id")
                for row in rows
                if isinstance(row, dict) and row.get("source_question_id") is not None
            ]
            if len(source_ids) != len(rows):
                unresolved_by_student[participant.student_id].add(task.pk)
                continue
        else:
            session = next(
                (
                    value
                    for value in participant.context_sessions
                    if value.class_task_id == task.pk
                ),
                None,
            )
            if session is None:
                unresolved_by_student[participant.student_id].add(task.pk)
                continue
            source_ids = [
                assignment.snapshot.source_question_id
                for assignment in session.assignments.all()
            ]
            if not source_ids:
                unresolved_by_student[participant.student_id].add(task.pk)
                continue
        active_by_student[participant.student_id].update(source_ids)

    return {
        student_id: StudentItemContext(
            completed_question_ids=frozenset(completed_by_student[student_id]),
            active_frozen_question_ids=frozenset(active_by_student[student_id]),
            unresolved_task_ids=tuple(sorted(unresolved_by_student[student_id])),
        )
        for student_id in student_ids
    }
