from django.core.management.base import BaseCommand
from django.db import transaction

from apps.experiments.models import PublishPlan
from apps.experiments.publication_services import database_now


class Command(BaseCommand):
    help = "Expire inactive A+ publish plans; request paths retain the same fallback."

    def handle(self, *args, **options):
        with transaction.atomic():
            now = database_now()
            count = PublishPlan.objects.filter(
                status__in=[PublishPlan.Status.DRAFT, PublishPlan.Status.PREVIEWED],
                expires_at__lte=now,
            ).update(status=PublishPlan.Status.EXPIRED)
        self.stdout.write(str(count))
