import json
import socket
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.analytics.research_materialization import (
    claim_session_materialization,
    renew_session_materialization_lease,
    run_session_materialization,
)
from apps.analytics.research_materialization_jobs import materialization_metrics


class Command(BaseCommand):
    help = "以任务租约协调并行生成会话研究派生结果"
    lease_seconds = 900
    heartbeat_interval = 60

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=2)

    def run_once(self, *, worker_id):
        from apps.analytics.runtime_pressure import runtime_pressure

        item = claim_session_materialization(
            worker_id=worker_id, lease_seconds=self.lease_seconds
        )
        if item is None:
            return False
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._renew_lease_until_stopped,
            kwargs={"item": item, "worker_id": worker_id, "stop": stop},
            name="research-materialization-lease",
            daemon=True,
        )
        heartbeat.start()
        try:
            run_session_materialization(item, worker_id=worker_id)
        finally:
            stop.set()
            heartbeat.join()
        pressure = runtime_pressure()
        if pressure['mode'] == 'recovering':
            time.sleep(pressure['retry_after_seconds'])
        if time.monotonic() - getattr(self, "_metrics_at", 0) >= 30:
            self.stdout.write(json.dumps(materialization_metrics()))
            self._metrics_at = time.monotonic()
        return True

    def _renew_lease_until_stopped(self, *, item, worker_id, stop):
        while not stop.wait(self.heartbeat_interval):
            close_old_connections()
            try:
                renewed = renew_session_materialization_lease(
                    item,
                    worker_id=worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if renewed is None:
                    return
            finally:
                close_old_connections()

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{id(self)}"
        while True:
            close_old_connections()
            processed = self.run_once(worker_id=worker_id)
            if options["once"]:
                return
            if not processed:
                time.sleep(max(1, options["interval"]))
