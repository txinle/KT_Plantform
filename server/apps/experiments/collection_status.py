from django.db.models import (
    Case,
    CharField,
    F,
    DateTimeField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Concat, Trim

from apps.assessment.models import ExperimentSession

from .models import ClassExperimentTask, ExperimentVersionParticipantSnapshot


COLLECTION_STATUS_CHOICES = (
    ("not_started", "未开始"),
    (ExperimentSession.Status.IN_PROGRESS, "进行中"),
    (ExperimentSession.Status.COMPLETED, "正常完成"),
    (ExperimentSession.Status.EXPIRED, "已超时"),
    (ExperimentSession.Status.INVALIDATED, "已作废"),
)
COLLECTION_STATUS_LABELS = dict(COLLECTION_STATUS_CHOICES)


def collection_class_tasks(experiment):
    return list(
        ClassExperimentTask.objects.filter(experiment=experiment)
        .select_related("class_group")
        .order_by("class_group__academic_year", "class_group__name", "pk")
    )


def collection_participant_queryset(
    experiment, *, class_id=None, status="", query=""
):
    session = ExperimentSession.objects.filter(
        participant_snapshot_id=OuterRef("pk"),
        class_task_id=OuterRef("class_task_id"),
    ).exclude(status=ExperimentSession.Status.PREPARED).annotate(
        public_status=Case(
            When(status=ExperimentSession.Status.SUBMITTING, then=Value(ExperimentSession.Status.IN_PROGRESS)),
            default=F("status"), output_field=CharField(),
        ),
    ).order_by("pk")
    snapshots = (
        ExperimentVersionParticipantSnapshot.objects.filter(
            class_task__experiment=experiment
        )
        .select_related("student", "class_task__class_group")
        .annotate(
            collection_status=Coalesce(
                Subquery(session.values("public_status")[:1], output_field=CharField()),
                Value("not_started"),
                output_field=CharField(),
            ),
            collection_started_at=Subquery(
                session.values("started_at")[:1], output_field=DateTimeField()
            ),
            collection_ended_at=Subquery(
                session.values("terminal_at")[:1], output_field=DateTimeField()
            ),
            collection_submission_count=Coalesce(
                Subquery(
                    session.values("submission_count")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
                output_field=IntegerField(),
            ),
            collection_full_name=Trim(
                Concat(
                    "student__first_name",
                    Value(" "),
                    "student__last_name",
                )
            ),
        )
    )
    if class_id is not None:
        snapshots = snapshots.filter(class_task__class_group_id=class_id)
    if status:
        snapshots = snapshots.filter(collection_status=status)
    if query:
        snapshots = snapshots.filter(
            Q(student__student_number__icontains=query)
            | Q(student__display_name__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(student__username__icontains=query)
            | Q(collection_full_name__icontains=query)
        )
    return snapshots.order_by(
        "class_task__class_group__academic_year",
        "class_task__class_group__name",
        "student__student_number",
        "pk",
    )


def collection_class_choice_projection(task):
    return {
        "id": task.class_group_id,
        "name": str(task.class_group),
    }


def collection_participant_projection(snapshot):
    student = snapshot.student
    class_group = snapshot.class_task.class_group
    return {
        "participant_id": snapshot.pk,
        "student_id": student.pk,
        "student_number": student.student_number or student.username,
        "display_name": student.shown_name,
        "class_id": class_group.pk,
        "class_name": str(class_group),
        "status": snapshot.collection_status,
        "status_label": COLLECTION_STATUS_LABELS[snapshot.collection_status],
        "started_at": snapshot.collection_started_at,
        "ended_at": snapshot.collection_ended_at,
        "submission_count": snapshot.collection_submission_count,
    }
