from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from apps.experiments.models import ClassExperimentTask, Experiment


@dataclass(frozen=True)
class TaskFact:
    task_id: int
    experiment_id: int
    experiment_name: str
    experiment_status: str
    release_snapshot_id: int
    class_snapshot_id: object
    published_at: datetime


@contextmanager
def analytics_read_transaction():
    """Provide one consistent read boundary for a complete analytics DTO."""
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
        yield timezone.now()


def resolve_task_facts(class_group, *, experiment_id=None):
    tasks = (
        ClassExperimentTask.objects.filter(
            class_group=class_group,
            experiment__course_id=class_group.course_id,
            experiment__status__in=(
                Experiment.Status.FROZEN,
                Experiment.Status.PUBLISHED,
                Experiment.Status.CLOSED,
            ),
            release_snapshot__experiment_id=F("experiment_id"),
            class_snapshot__version_id=F("release_snapshot_id"),
            class_snapshot__source_class_group_id=F("class_group_id"),
            published_at=F("release_snapshot__published_at"),
        )
        .select_related("experiment")
        .order_by("-published_at", "-pk")
    )
    if experiment_id is not None:
        tasks = tasks.filter(experiment_id=experiment_id)
    return tuple(
        TaskFact(
            task_id=task.pk,
            experiment_id=task.experiment_id,
            experiment_name=task.experiment.name,
            experiment_status=task.experiment.status,
            release_snapshot_id=task.release_snapshot_id,
            class_snapshot_id=task.class_snapshot_id,
            published_at=task.published_at,
        )
        for task in tasks
    )
