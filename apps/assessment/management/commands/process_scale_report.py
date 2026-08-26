from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.assessment.models import AssessmentEvent


class Command(BaseCommand):
    help = "报告过程事件规模、分域数量及 PostgreSQL 分区阈值"

    def handle(self, *args, **options):
        counts = dict(AssessmentEvent.objects.values_list("event_domain").annotate(total=Count("event_id")))
        self.stdout.write("partition_threshold_events=20000000")
        self.stdout.write("partition_threshold_table_gb=20")
        self.stdout.write("partition_threshold_monthly_events=5000000")
        self.stdout.write(f"assessment_events={counts.get(AssessmentEvent.EventDomain.ASSESSMENT, 0)}")
        self.stdout.write(f"telemetry_events={counts.get(AssessmentEvent.EventDomain.TELEMETRY, 0)}")
        self.stdout.write(f"system_events={counts.get(AssessmentEvent.EventDomain.SYSTEM, 0)}")
