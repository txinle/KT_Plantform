import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import DataPurgeRequest
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.snapshot_services import create_simulation_snapshot

from .research_deletion import (
    ARTIFACT_CLEANUP_SCOPE,
    cleanup_state,
    process_artifact_cleanup,
    retry_artifact_cleanups,
    schedule_artifact_cleanup,
)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL durable cleanup worker")
class ResearchArtifactCleanupWorkerTests(TransactionTestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def _file(self, name="artifact.csv"):
        path = Path(self.media.name) / "exports" / "test-job" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private research output", encoding="utf-8")
        return path

    def _schedule(self, files=None):
        files = files if files is not None else [{"kind": "export", "path": str(self._file())}]
        cleanup_id = schedule_artifact_cleanup(
            course_id=self.course.pk, files=files, reason="test", actor=self.teacher,
        )
        return DataPurgeRequest.objects.get(purge_request_id=cleanup_id)

    def _job(self, cleanup):
        return apps.get_model("analytics", "ResearchArtifactCleanupJob").objects.get(cleanup=cleanup)

    def _thread(self, function, *args, **kwargs):
        close_old_connections()
        try:
            return function(*args, **kwargs)
        finally:
            connection.close()

    def test_committed_schedule_keeps_files_until_worker_runs(self):
        path = self._file()
        with transaction.atomic():
            cleanup = self._schedule([{"kind": "export", "path": str(path)}])
            self.assertTrue(path.exists())
        self.assertTrue(path.exists(), "Commit must not perform filesystem cleanup")
        self.assertEqual(self._job(cleanup).state, "pending")
        self.assertEqual(cleanup_state(cleanup.purge_request_id)["state"], "pending")
        self.assertTrue(path.exists())

    def test_original_file_scope_is_not_replaced_by_remaining_work(self):
        path = self._file()
        cleanup = self._schedule([{"kind": "export", "path": str(path)}])
        original = {"course_id": self.course.pk, "files": [{"kind": "export", "path": str(path)}]}
        self.assertTrue(process_artifact_cleanup(cleanup.pk))
        cleanup.refresh_from_db()
        self.assertEqual(cleanup.scope_definition, original)
        self.assertEqual(self._job(cleanup).remaining_files, [])

    def test_rollback_removes_receipt_and_job_without_touching_file(self):
        path = self._file()
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                self._schedule([{"kind": "export", "path": str(path)}])
                raise RuntimeError("rollback")
        self.assertFalse(DataPurgeRequest.objects.filter(scope_type=ARTIFACT_CLEANUP_SCOPE).exists())
        self.assertEqual(apps.get_model("analytics", "ResearchArtifactCleanupJob").objects.count(), 0)
        self.assertTrue(path.exists())

    def test_file_io_runs_outside_database_transaction_and_get_is_read_only(self):
        path = self._file()
        cleanup = self._schedule([{"kind": "export", "path": str(path)}])
        unlink = Path.unlink
        observed = []

        def delete(file, *args, **kwargs):
            observed.append(connection.in_atomic_block)
            return unlink(file, *args, **kwargs)

        with patch.object(Path, "unlink", new=delete):
            state = cleanup_state(cleanup.purge_request_id)
            self.assertEqual(state["state"], "pending")
            self.assertEqual(observed, [])
            self.assertTrue(process_artifact_cleanup(cleanup.pk))
        self.assertEqual(observed, [False])

    def test_failure_is_delayed_and_reaches_explicit_eight_attempt_limit(self):
        from .research_deletion import claim_artifact_cleanup, run_artifact_cleanup

        path = self._file()
        cleanup = self._schedule([{"kind": "export", "path": str(path)}])
        now = timezone.now()
        delays = [5, 10, 20, 40, 80, 160, 300]
        for attempt in range(1, 9):
            with patch("apps.analytics.research_deletion.timezone.now", return_value=now):
                claimed = claim_artifact_cleanup(cleanup_pk=cleanup.pk)
                self.assertIsNotNone(claimed)
                with patch.object(Path, "unlink", side_effect=PermissionError("private path /secret")):
                    self.assertFalse(run_artifact_cleanup(claimed))
                job = self._job(cleanup)
                self.assertEqual(job.attempts, attempt)
                if attempt < 8:
                    self.assertEqual(job.state, "pending")
                    self.assertEqual(job.next_attempt_at, now + timedelta(seconds=delays[attempt - 1]))
                    self.assertIsNone(claim_artifact_cleanup(cleanup_pk=cleanup.pk))
                    self.assertEqual(retry_artifact_cleanups(course_id=self.course.pk), 0)
                    now = job.next_attempt_at
                else:
                    self.assertEqual(job.state, "failed")
                    self.assertIsNone(claim_artifact_cleanup(cleanup_pk=cleanup.pk))
        state = cleanup_state(cleanup.purge_request_id)
        self.assertTrue(state["cleanup_pending"])
        self.assertEqual((state["state"], state["attempts"], state["next_retry_at"]), ("failed", 8, None))
        self.assertNotIn("secret", state["error"])
        self.assertTrue(path.exists())

    def test_restart_after_file_delete_recovers_expired_lease_and_fences_old_worker(self):
        from .research_deletion import claim_artifact_cleanup, run_artifact_cleanup

        path = self._file()
        cleanup = self._schedule([{"kind": "export", "path": str(path)}])
        before_crash = timezone.now() - timedelta(seconds=100)
        job = self._job(cleanup)
        type(job).objects.filter(pk=job.pk).update(next_attempt_at=before_crash)
        old = claim_artifact_cleanup(cleanup_pk=cleanup.pk, now=before_crash)
        path.unlink()  # Simulate a process dying after I/O and before its DB write.
        replacement = claim_artifact_cleanup(cleanup_pk=cleanup.pk)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(old.lease_token, replacement.lease_token)
        self.assertFalse(run_artifact_cleanup(old))
        self.assertTrue(run_artifact_cleanup(replacement))
        self.assertEqual(self._job(cleanup).state, "completed")
        self.assertEqual(self._job(cleanup).attempts, 2)

    def test_worker_losing_lease_during_io_cannot_write_completion(self):
        from .research_deletion import claim_artifact_cleanup, run_artifact_cleanup

        cleanup = self._schedule()
        old = claim_artifact_cleanup(cleanup_pk=cleanup.pk)
        real_unlink = Path.unlink
        replacements = []

        def delete(path, *args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            type(old).objects.filter(pk=old.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            replacements.append(claim_artifact_cleanup(cleanup_pk=cleanup.pk))
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=delete):
            self.assertFalse(run_artifact_cleanup(old))
        self.assertEqual(self._job(cleanup).state, "running")
        self.assertEqual(self._job(cleanup).lease_token, replacements[0].lease_token)
        self.assertTrue(run_artifact_cleanup(replacements[0]))

    def test_storage_backend_failure_is_recorded_for_retry(self):
        class StorageUnavailable(Exception):
            pass

        cleanup = self._schedule([{"kind": "storage", "path": "simulations/unavailable.bin"}])
        with patch("apps.analytics.research_deletion.default_storage.delete", side_effect=StorageUnavailable("private endpoint")):
            try:
                result = process_artifact_cleanup(cleanup.pk)
            except StorageUnavailable:
                self.fail("Storage backend failure escaped without scheduling its retry")
        self.assertFalse(result)
        self.assertEqual(self._job(cleanup).state, "pending")
        self.assertEqual(self._job(cleanup).attempts, 1)
        self.assertIn("StorageUnavailable", self._job(cleanup).last_error)
        self.assertNotIn("endpoint", cleanup_state(cleanup.purge_request_id)["error"])

    def test_duplicate_workers_claim_once_and_skip_a_locked_job(self):
        from .research_deletion import claim_artifact_cleanup

        cleanup = self._schedule()
        barrier = Barrier(2)

        def claim():
            barrier.wait(timeout=5)
            item = claim_artifact_cleanup(cleanup_pk=cleanup.pk)
            return item.pk if item else None

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._thread, claim) for _ in range(2)]
            claimed = [future.result(timeout=10) for future in futures]
        self.assertEqual(sum(item is not None for item in claimed), 1)
        self.assertEqual(self._job(cleanup).attempts, 1)
        job = self._job(cleanup)
        type(job).objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        with transaction.atomic():
            type(job).objects.select_for_update().get(pk=job.pk)
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self._thread, claim_artifact_cleanup, cleanup_pk=cleanup.pk).result(timeout=3)
                self.assertIsNone(result)

    def test_eighth_expired_claim_becomes_failed_without_a_ninth_file_attempt(self):
        from .research_deletion import claim_artifact_cleanup

        cleanup = self._schedule()
        job = self._job(cleanup)
        type(job).objects.filter(pk=job.pk).update(
            state="running", attempts=8, lease_token=uuid4(),
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(claim_artifact_cleanup(cleanup_pk=cleanup.pk))
        self.assertEqual(self._job(cleanup).state, "failed")
        cleanup.refresh_from_db()
        self.assertEqual(cleanup.status, DataPurgeRequest.Status.FAILED)

    def test_shared_storage_is_retained_with_audit_reason(self):
        key = "simulations/shared.bin"
        path = Path(self.media.name) / key
        path.parent.mkdir(parents=True)
        path.write_bytes(b"shared")
        create_simulation_snapshot(
            attempt=self.attempt, simulation_version="v1", state_metadata={"source": "shared"},
            blob_storage_key=key, content_bytes=b"shared",
        )
        cleanup = self._schedule([{"kind": "storage", "path": key}])
        self.assertTrue(process_artifact_cleanup(cleanup.pk))
        self.assertTrue(path.exists())
        retained = self._job(cleanup).retained_files
        self.assertEqual(retained[0]["path"], key)
        self.assertEqual(retained[0]["reason"], "still_referenced")
        self.assertEqual(cleanup_state(cleanup.purge_request_id)["retained_count"], 1)

    def test_unreferenced_storage_and_missing_files_finish_idempotently(self):
        key = "simulations/retired.bin"
        path = Path(self.media.name) / key
        path.parent.mkdir(parents=True)
        path.write_bytes(b"retired")
        missing = Path(self.media.name) / "exports" / "missing.csv"
        cleanup = self._schedule([
            {"kind": "storage", "path": key}, {"kind": "export", "path": str(missing)},
        ])
        self.assertTrue(process_artifact_cleanup(cleanup.pk))
        self.assertFalse(path.exists())
        self.assertTrue(process_artifact_cleanup(cleanup.pk))
        self.assertEqual(self._job(cleanup).attempts, 1)

    def test_invalid_paths_and_unknown_kind_are_not_deleted_or_silently_completed(self):
        outside = Path(self.media.name) / "independent.txt"
        outside.write_text("keep", encoding="utf-8")
        export_root = Path(self.media.name) / "exports"
        export_root.mkdir()
        cleanup = self._schedule([
            {"kind": "export", "path": str(outside)},
            {"kind": "export", "path": str(export_root)},
            {"kind": "mystery", "path": str(outside)},
            {"kind": "storage", "path": "../independent.txt"},
        ])
        self.assertFalse(process_artifact_cleanup(cleanup.pk))
        self.assertTrue(outside.exists())
        self.assertTrue(export_root.is_dir())
        self.assertEqual(len(self._job(cleanup).remaining_files), 4)
        self.assertEqual(self._job(cleanup).state, "pending")

    def test_heartbeat_renews_during_file_io_and_rejects_stale_token(self):
        from .research_deletion import claim_artifact_cleanup, renew_artifact_cleanup_lease, run_artifact_cleanup

        cleanup = self._schedule()
        job = claim_artifact_cleanup(cleanup_pk=cleanup.pk)
        original_expiry = job.lease_expires_at
        renewed = Event()
        real_renew = renew_artifact_cleanup_lease
        real_unlink = Path.unlink

        def renew(*args, **kwargs):
            result = real_renew(*args, **kwargs)
            if result:
                renewed.set()
            return result

        def delete(path, *args, **kwargs):
            self.assertTrue(renewed.wait(timeout=3))
            self.assertFalse(connection.in_atomic_block)
            self.assertGreater(self._job(cleanup).lease_expires_at, original_expiry)
            return real_unlink(path, *args, **kwargs)

        with patch("apps.analytics.research_deletion.CLEANUP_HEARTBEAT_SECONDS", 0.02), patch(
            "apps.analytics.research_deletion.renew_artifact_cleanup_lease", side_effect=renew,
        ), patch.object(Path, "unlink", new=delete):
            self.assertTrue(run_artifact_cleanup(job))
        self.assertFalse(renew_artifact_cleanup_lease(job.pk, lease_token=job.lease_token))

    def test_command_recovers_expired_pending_receipts_and_runs_one_cleanup(self):
        from apps.experiments.deletion_models import ResearchDeletionOperation

        operation = ResearchDeletionOperation.objects.create(
            operation_id=uuid4(), actor=self.teacher, course=self.course,
            action="delete_experiment", object_id=self.experiment.pk,
            scope_fingerprint="f" * 64, frozen_scope={}, confirmation_token_hash="a" * 64,
            execute_before=timezone.now() - timedelta(seconds=1),
        )
        cleanup = self._schedule()
        call_command("run_research_artifact_cleanups", once=True, stdout=io.StringIO())
        operation.refresh_from_db()
        self.assertEqual(operation.state, "rejected")
        self.assertEqual(self._job(cleanup).state, "completed")

    def test_export_worker_does_not_consume_cleanup_queue(self):
        cleanup = self._schedule()
        with patch("apps.analytics.research_deletion.retry_artifact_cleanups") as drain:
            call_command("run_export_jobs", once=True, stdout=io.StringIO())
        drain.assert_not_called()
        self.assertEqual(self._job(cleanup).state, "pending")


@skipUnless(connection.vendor == "postgresql", "PostgreSQL cleanup queue migration")
class ResearchArtifactCleanupMigrationTests(TransactionTestCase):
    def test_reverse_migration_refuses_to_drop_unfinished_queue(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        cleanup_id = schedule_artifact_cleanup(
            course_id=self.course.pk, files=[{"kind": "export", "path": "/pending.csv"}],
            reason="pending", actor=self.teacher,
        )
        with self.assertRaisesMessage(RuntimeError, "清理队列尚未排空"):
            MigrationExecutor(connection).migrate([("analytics", "0024_research_publication_sequence")])
        self.assertTrue(apps.get_model("analytics", "ResearchArtifactCleanupJob").objects.filter(
            cleanup__purge_request_id=cleanup_id, state="pending",
        ).exists())

    def test_legacy_incomplete_scope_is_copied_without_reconstructing_lost_files(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate([("analytics", "0024_research_publication_sequence")])
            remaining = [{"kind": "export", "path": "/legacy/remaining.csv"}]
            cleanup = DataPurgeRequest.objects.create(
                scope_type=ARTIFACT_CLEANUP_SCOPE,
                scope_definition={"course_id": self.course.pk, "files": remaining},
                requested_by=self.teacher, reason_code="old", status="failed",
                result_counts={"remaining_files": 1, "error": "old failure"},
            )
            executor = MigrationExecutor(connection)
            executor.migrate([("analytics", "0025_research_artifact_cleanup_job")])
            historical = executor.loader.project_state([("analytics", "0025_research_artifact_cleanup_job")]).apps
            job = historical.get_model("analytics", "ResearchArtifactCleanupJob").objects.get(cleanup_id=cleanup.pk)
            self.assertEqual(job.remaining_files, remaining)
            self.assertEqual((job.attempts, job.state, job.retained_files), (0, "pending", []))
            cleanup.refresh_from_db()
            self.assertEqual(cleanup.scope_definition["files"], remaining)
        finally:
            MigrationExecutor(connection).migrate(latest)
