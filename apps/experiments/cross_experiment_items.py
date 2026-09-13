from dataclasses import dataclass

from django.utils import timezone

from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.models import SubmissionEvent

from .models import ExperimentVersionParticipantSnapshot


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
