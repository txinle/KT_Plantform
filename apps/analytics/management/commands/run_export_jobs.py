import socket

from django.core.management.base import BaseCommand

from apps.analytics.services import claim_export_job, run_export_job


class Command(BaseCommand):
    help = "领取并生成一个或多个研究导出任务"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{id(self)}"
        count = 0
        while True:
            job = claim_export_job(worker_id=worker_id)
            if not job:
                break
            try:
                run_export_job(job)
                count += 1
            except Exception:
                job.status = job.Status.FAILED
                job.retry_count += 1
                job.save(update_fields=["status", "retry_count"])
                raise
            if options["once"]:
                break
        self.stdout.write(self.style.SUCCESS(f"processed={count}"))
