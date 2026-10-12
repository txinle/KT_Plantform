from django.core.management.base import BaseCommand

from apps.assessment.legacy_projection import project_legacy_events


class Command(BaseCommand):
    help = "将历史业务访问/草稿/提交投影为独立 legacy_* 过程事件"

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"created={project_legacy_events()}"))
