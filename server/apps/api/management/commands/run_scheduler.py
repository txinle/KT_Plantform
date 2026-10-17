import os
import logging
import time
from contextlib import contextmanager

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run idempotent KT background jobs under one PostgreSQL advisory lock."
    lock_id = 4_819_520_817

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def try_acquire_lock(self):
        if connection.vendor != "postgresql":
            return True
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [self.lock_id])
            return bool(cursor.fetchone()[0])

    def release_lock(self):
        if connection.vendor != "postgresql":
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [self.lock_id])

    @contextmanager
    def phase_timing(self, name):
        started = time.monotonic()
        status = 'failed'
        try:
            yield
            status = 'completed'
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            self.stdout.write(f'scheduler_phase name={name} status={status} duration_ms={duration_ms:.3f}')

    def run_once(self):
        if (settings.ASSESSMENT_ADMISSION_GLOBAL_ENABLED
                and settings.ASSESSMENT_PREPARED_ADMISSION_MODE == 'redis'):
            from apps.assessment.admission_reconciliation import reconcile_admission_page
            from apps.assessment.admission import AdmissionUnavailable
            from django.db import DatabaseError
            try:
                with self.phase_timing('reconcile_admission'):
                    result = reconcile_admission_page(cursor=getattr(self, '_admission_cursor', 0), limit=64)
                    self._admission_cursor = result['cursor']
            except (AdmissionUnavailable, DatabaseError):
                logger.warning('Admission reconciliation unavailable; occupied executions retained')
        phases = ['expire_publish_plans', 'expire_due_sessions',
                  'run_student_import_jobs', 'reconcile_research_materializations']
        if os.getenv("RUN_SCHEDULER_EXPORT_JOBS", "1") == "1":
            phases.append('run_export_jobs')
        for phase in phases:
            with self.phase_timing(phase):
                call_command(phase)

    def handle(self, *args, **options):
        if not self.try_acquire_lock():
            self.stdout.write("scheduler lock is held by another process")
            return
        try:
            while True:
                self.run_once()
                if options["once"]:
                    return
                time.sleep(max(1, options["interval"]))
        finally:
            self.release_lock()
