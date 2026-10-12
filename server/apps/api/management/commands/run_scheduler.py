import os
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection


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

    def run_once(self):
        call_command("expire_publish_plans")
        call_command("expire_due_sessions")
        call_command("run_student_import_jobs")
        call_command("reconcile_research_materializations")
        if os.getenv("RUN_SCHEDULER_EXPORT_JOBS", "1") == "1":
            call_command("run_export_jobs")

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
