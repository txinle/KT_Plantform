from django.core.management.base import BaseCommand

from apps.analytics.services import cleanup_expired_exports


class Command(BaseCommand):
    help = "删除过期导出文件并标记任务"

    def handle(self, *args, **options):
        from apps.analytics.research_deletion import retry_artifact_cleanups
        retry_artifact_cleanups()
        removed = cleanup_expired_exports()
        self.stdout.write(self.style.SUCCESS(f"removed={removed}"))
