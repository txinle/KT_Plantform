import json

from django.core.management.base import BaseCommand

from apps.analytics.research_materialization_jobs import (
    materialization_metrics,
    reconcile_session_materializations,
)


class Command(BaseCommand):
    help = "限速补建缺失或已过期的会话研究派生结果"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        result = reconcile_session_materializations(limit=options["limit"])
        self.stdout.write(json.dumps({**result, **materialization_metrics()}))
