import json
import socket
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from apps.analytics.research_materialization import (
    claim_session_materialization,
    renew_session_materialization_lease,
    run_session_materialization,
)
from apps.analytics.research_materialization_jobs import materialization_metrics


class Command(BaseCommand):
    help = "以单并发 worker 生成会话研究派生结果"
    lock_id = 4_819_520_818
    lease_seconds = 900
    heartbeat_interval = 60

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=2)

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

    def run_once(self, *, worker_id):
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
        self.stdout.write(json.dumps(materialization_metrics()))
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
        if not self.try_acquire_lock():
            self.stdout.write("research materialization worker lock is held")
            return
        worker_id = f"{socket.gethostname()}:{id(self)}"
        try:
            while True:
                processed = self.run_once(worker_id=worker_id)
                if options["once"]:
                    return
                if not processed:
                    time.sleep(max(1, options["interval"]))
        finally:
            self.release_lock()
