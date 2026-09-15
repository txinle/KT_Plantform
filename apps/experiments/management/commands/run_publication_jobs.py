import os
import socket
import time

from django.core.management.base import BaseCommand

from apps.experiments.publication_jobs import run_next_publish_operation


class Command(BaseCommand):
    help = "Run queued experiment publication jobs in a dedicated worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            job = run_next_publish_operation(worker_id=worker_id)
            if options["once"]:
                return
            if job is None:
                time.sleep(max(options["poll_seconds"], 0.1))
