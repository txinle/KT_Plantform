from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from apps.assessment.models import ExperimentSession
from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.services import _terminate_for_deadline, _validate_session_time


TERMINAL_PROJECTION_GRACE = timedelta(seconds=60)


class Command(BaseCommand):
    help = "Idempotently close due task-backed assessment sessions."

    def handle(self, *args, **options):
        count = 0
        now = timezone.now()
        session_ids = list(
            ExperimentSession.objects.filter(
                status=ExperimentSession.Status.IN_PROGRESS,
            ).filter(
                Q(deadline_at__lte=now)
                | Q(class_task__cancelled_at__lte=now)
                | Q(class_task__invalidated_at__lte=now)
                | Q(class_task__manual_closed_at__lte=now)
                | Q(class_task__schedule_revisions__closes_at__lte=now)
            ).values_list("pk", flat=True).distinct()
        )
        for session_id in session_ids:
            with transaction.atomic():
                session = (
                    ExperimentSession.objects.select_for_update(of=("self",), skip_locked=True)
                    .select_related("class_task")
                    .filter(pk=session_id)
                    .first()
                )
                if session is None or session.status != ExperimentSession.Status.IN_PROGRESS:
                    continue
                task = session.class_task
                terminal_at = None
                if task is not None:
                    state = DeadlineDecisionService.get_task_state(task, at=now)
                    if state == "manual_closed":
                        terminal_at = DeadlineDecisionService.manual_close_at(task, now)
                    elif state == "cancelled":
                        terminal_at = task.cancelled_at
                    elif state == "invalidated":
                        terminal_at = task.invalidated_at
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
