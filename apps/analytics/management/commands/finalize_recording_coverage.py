from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.assessment.models import ExperimentSession
from apps.analytics.recording_coverage import generate_session_coverage


class Command(BaseCommand):
    help = "Finalize versioned recording-coverage runs after the frozen 300-second grace period."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=300)
        sessions = ExperimentSession.objects.filter(
            status__in=("completed", "expired", "invalidated"),
            terminal_at__lte=cutoff,
        ).select_related("capture_contract")
        created = 0
        for session in sessions.iterator():
            before = session.recording_coverage_results.count()
            generate_session_coverage(session, force=False)
            created += session.recording_coverage_results.count() > before
        self.stdout.write(self.style.SUCCESS(f"Finalized {created} recording coverage run(s)."))
