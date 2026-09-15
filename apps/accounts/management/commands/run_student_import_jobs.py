import socket

from django.core.management.base import BaseCommand

from apps.accounts.student_import_jobs import (
    StudentImportJobError,
    claim_student_import_job,
    fail_student_import_job,
    retry_student_import_job,
    run_student_import_job,
)


class Command(BaseCommand):
    help = "领取并处理一个或多个课程班级学生名单导入任务"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{id(self)}"
        count = 0
        while True:
            job = claim_student_import_job(worker_id=worker_id)
            if job is None:
                break
            try:
                run_student_import_job(job, worker_id=worker_id)
                count += 1
            except StudentImportJobError as error:
                fail_student_import_job(job, error)
            except Exception:
                retry_student_import_job(job)
            if options["once"]:
                break
        self.stdout.write(self.style.SUCCESS(f"processed={count}"))
