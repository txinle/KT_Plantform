import socket
import time

from django.core.management.base import BaseCommand

from apps.analytics.services import claim_export_job, run_export_job


class Command(BaseCommand):
    help = "领取并生成一个或多个研究导出任务"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        from apps.analytics.runtime_pressure import runtime_pressure
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
                self._handle_job_failure(job, None)
            pressure = runtime_pressure()
            if pressure['mode'] == 'recovering':
                time.sleep(pressure['retry_after_seconds'])
            if options["once"]:
                break
        self.stdout.write(self.style.SUCCESS(f"processed={count}"))

    def _handle_job_failure(self, job, _error):
        claimed_by = job.locked_by
        job.retry_count += 1
        job.locked_by = ""
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.status = job.Status.QUEUED if job.retry_count < 3 else job.Status.FAILED
        type(job).objects.filter(pk=job.pk, locked_by=claimed_by, status__in=[job.Status.QUEUED, job.Status.RUNNING]).update(
            retry_count=job.retry_count, locked_by="", lease_expires_at=None,
            heartbeat_at=None, status=job.status,
        )
