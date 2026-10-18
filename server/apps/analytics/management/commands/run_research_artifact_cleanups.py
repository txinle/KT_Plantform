import json
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.analytics.research_deletion import claim_artifact_cleanup, run_artifact_cleanup
from apps.experiments.deletion_operations import recover_pending_deletion_operations


class Command(BaseCommand):
    help = "通过持久租约清理已确认删除的研究文件，并恢复未执行的删除回执"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=2)

    def run_once(self):
        recovered = recover_pending_deletion_operations()
        job = claim_artifact_cleanup()
        completed = run_artifact_cleanup(job) if job is not None else False
        if job is not None or recovered:
            self.stdout.write(json.dumps({"claimed": int(job is not None), "completed": int(completed), "recovered_operations": recovered}))
        return job is not None

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            processed = self.run_once()
            if options["once"]:
                return
            if not processed:
                time.sleep(max(1, options["interval"]))
