from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assessment.models import ExperimentSession
from apps.assessment.services import _validate_session_time
from apps.experiments.models import ClassExperimentTask


class Command(BaseCommand):
    help = "Idempotently close due task-backed assessment sessions."

    def handle(self, *args, **options):
        count = 0
        now = timezone.now()
        for session_id in ExperimentSession.objects.filter(
            status=ExperimentSession.Status.IN_PROGRESS,
        ).values_list("pk", flat=True).iterator():
            with transaction.atomic():
                task_id = ExperimentSession.objects.only("class_task_id").get(
                    pk=session_id
                ).class_task_id
                if task_id is not None:
                    ClassExperimentTask.objects.select_for_update().get(pk=task_id)
                session = ExperimentSession.objects.select_for_update().get(pk=session_id)
                if _validate_session_time(session, request_received_at=now):
                    count += 1
        self.stdout.write(str(count))
