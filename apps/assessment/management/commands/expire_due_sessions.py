from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.assessment.models import ExperimentSession
from apps.assessment.services import _terminate_for_deadline, _validate_session_time


TERMINAL_PROJECTION_GRACE = timedelta(seconds=60)


class Command(BaseCommand):
    help = "Idempotently close due task-backed assessment sessions."

    def handle(self, *args, **options):
        count = 0
        now = timezone.now()
        for session_id in ExperimentSession.objects.filter(
            status=ExperimentSession.Status.IN_PROGRESS,
        ).values_list("pk", flat=True).iterator():
            with transaction.atomic():
                session = (
                    ExperimentSession.objects.select_for_update(of=("self",))
                    .select_related("class_task")
                    .get(pk=session_id)
                )
                task = session.class_task
                terminal_at = (
                    task.manual_closed_at or task.cancelled_at or task.invalidated_at
                    if task is not None
                    else None
                )
                if terminal_at is not None and now >= terminal_at + TERMINAL_PROJECTION_GRACE:
                    _terminate_for_deadline(
                        session,
                        now=terminal_at,
                        reason="task_terminated",
                    )
                    count += 1
                elif _validate_session_time(session, request_received_at=now):
                    count += 1
        self.stdout.write(str(count))
