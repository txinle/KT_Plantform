import json
import time
from uuid import uuid4
from unittest.mock import patch

from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.analytics import test_teacher_data_center_api as fixtures
from apps.analytics.models import ResearchSessionMaterialization
from apps.assessment.models import (
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    SubmissionEvent,
)
from apps.accounts.models import ClassGroup, CourseEnrollment, User
from apps.experiments.models import ExperimentClassSnapshot


@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class ResearchMaterializationJobTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def _another_session(self, index):
        student = User.objects.create_user(
            username=f"materialization-job-student-{index}",
            role=User.Role.STUDENT,
            student_number=f"MAT-JOB-{index}",
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=self.class_group, student=student
        )
        return ExperimentSession.objects.create(
            version=self.version,
            student=student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )

    def test_atomic_submission_context_coalesces_revision_and_callback(self):
        from .research_materialization_jobs import coalesce_session_materializations, schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with coalesce_session_materializations(), transaction.atomic():
                schedule_session_materialization(self.session.pk)
                schedule_session_materialization(self.session.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 1)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(self.session.research_materializations.get().source_revision, 1)

    def test_coalesced_context_keeps_different_sessions_separate(self):
        from .research_materialization_jobs import coalesce_session_materializations, schedule_session_materialization

        another = self._another_session(400)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with coalesce_session_materializations(), transaction.atomic():
                schedule_session_materialization(self.session.pk)
                schedule_session_materialization(another.pk)
                schedule_session_materialization(self.session.pk)
        self.session.refresh_from_db()
        another.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 1)
        self.assertEqual(another.research_source_revision, 1)
        self.assertEqual(len(callbacks), 2)

    def test_savepoint_rollback_removes_coalescing_evidence(self):
        from .research_materialization_jobs import coalesce_session_materializations, schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with coalesce_session_materializations(), transaction.atomic():
                try:
                    with transaction.atomic():
                        schedule_session_materialization(self.session.pk)
                        raise ValueError('rollback only the first schedule')
                except ValueError:
                    pass
                schedule_session_materialization(self.session.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 1)
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(self.session.research_materializations.exists())

    def test_complete_bundle_rollback_leaves_no_dirty_fact_or_callback(self):
        from .research_materialization_jobs import coalesce_session_materializations, schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(ValueError), coalesce_session_materializations(), transaction.atomic():
                schedule_session_materialization(self.session.pk)
                schedule_session_materialization(self.session.pk)
                raise ValueError('rollback bundle')
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 0)
        self.assertEqual(callbacks, [])
        self.assertFalse(self.session.research_materializations.exists())

    def test_outside_coalescing_context_keeps_two_revision_bumps(self):
        from .research_materialization_jobs import schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                schedule_session_materialization(self.session.pk)
                schedule_session_materialization(self.session.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 2)
        self.assertEqual(len(callbacks), 2)

    def test_reconcile_recovers_coalesced_dirty_fact_after_callback_failure(self):
        from .research_materialization_jobs import coalesce_session_materializations, reconcile_session_materializations, schedule_session_materialization

        with patch('apps.analytics.research_materialization_jobs._enqueue_if_eligible', side_effect=RuntimeError('queue unavailable')):
            with self.captureOnCommitCallbacks(execute=True):
                with coalesce_session_materializations(), transaction.atomic():
                    schedule_session_materialization(self.session.pk)
                    schedule_session_materialization(self.session.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, 1)
        self.assertIsNotNone(self.session.research_source_changed_at)
        self.assertFalse(self.session.research_materializations.exists())
        self.assertEqual(reconcile_session_materializations(limit=10)['enqueued'], 1)
        self.assertEqual(self.session.research_materializations.get().source_revision, 1)

    def test_terminal_session_schedules_one_persistent_job_after_commit(self):
        from .research_materialization_jobs import schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True):
            schedule_session_materialization(self.session.pk)

        self.assertEqual(self.session.research_materializations.count(), 1)

        self.session.refresh_from_db()
        queued = self.session.research_materializations.get()
        self.assertEqual(self.session.research_source_revision, 1)
        self.assertEqual(queued.source_revision, 1)

    def test_reconcile_uses_revision_index_without_full_source_fingerprint(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations

        self.session.research_source_revision = 3
        self.session.save(update_fields=["research_source_revision"])
        current = enqueue_session_materialization(self.session)
        current.status = current.Status.SUCCEEDED
        current.is_current = True
        current.source_revision = 2
        current.finished_at = timezone.now()
        current.save(
            update_fields=[
                "status", "is_current", "source_revision", "finished_at"
            ]
        )

        with patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            side_effect=AssertionError("reconcile must not hash source tables"),
        ):
            result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 1, "enqueued": 1})
        queued = self.session.research_materializations.get(status="queued")
        self.assertEqual(queued.source_revision, 3)

    def test_lifecycle_callback_does_not_turn_a_committed_write_into_an_error(self):
        from .research_materialization_jobs import schedule_session_materialization

        with patch(
            "apps.analytics.research_materialization_jobs._enqueue_if_eligible",
            side_effect=RuntimeError("queue unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                schedule_session_materialization(self.session.pk)

    def test_lifecycle_callback_does_not_compute_a_source_fingerprint(self):
        from .research_materialization_jobs import schedule_session_materialization

        with patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            side_effect=AssertionError("lifecycle callback computed a full fingerprint"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                schedule_session_materialization(self.session.pk)

        self.assertTrue(self.session.research_materializations.exists())

    def test_finish_session_schedules_materialization_after_domain_commit(self):
        from apps.assessment.services import finish_session

        self.session.status = ExperimentSession.Status.IN_PROGRESS
        self.session.completed_at = None
        self.session.terminal_at = None
        self.session.save(update_fields=["status", "completed_at", "terminal_at"])

        with patch("apps.assessment.services._append_session_event"):
            with self.captureOnCommitCallbacks(execute=True):
                finish_session(self.session.pk)

        self.assertTrue(self.session.research_materializations.filter(status="queued").exists())

    def test_disabled_course_does_not_schedule_materialization(self):
        from .research_materialization_jobs import schedule_session_materialization

        with override_settings(ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"999999"})):
            with self.captureOnCommitCallbacks(execute=True):
                schedule_session_materialization(self.session.pk)

        self.assertFalse(self.session.research_materializations.exists())

    def test_reconcile_backfills_missing_and_stale_terminal_sessions_only(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations

        first = enqueue_session_materialization(self.session)
        first.status = first.Status.SUCCEEDED
        first.is_current = True
        first.finished_at = timezone.now()
        first.save(update_fields=["status", "is_current", "finished_at"])

        stale_session = self._another_session(1)
        missing_session = self._another_session(2)
        in_progress = self._another_session(3)
        in_progress.status = ExperimentSession.Status.IN_PROGRESS
        in_progress.completed_at = None
        in_progress.terminal_at = None
        in_progress.save(update_fields=["status", "completed_at", "terminal_at"])
        invalidated = self._another_session(4)
        invalidated.status = ExperimentSession.Status.INVALIDATED
        invalidated.terminal_at = timezone.now()
        invalidated.save(update_fields=["status", "terminal_at"])

        stale_current = enqueue_session_materialization(stale_session)
        stale_current.status = stale_current.Status.SUCCEEDED
        stale_current.is_current = True
        stale_current.projection_version = "old-version"
        stale_current.save(update_fields=["status", "is_current", "projection_version"])

        result = reconcile_session_materializations(limit=10)

        self.assertEqual(result["enqueued"], 2)
        self.assertTrue(missing_session.research_materializations.filter(status="queued").exists())
        self.assertTrue(stale_session.research_materializations.filter(status="queued").exists())
        self.assertFalse(in_progress.research_materializations.exists())
        self.assertFalse(invalidated.research_materializations.exists())
        self.assertFalse(self.session.research_materializations.filter(status="queued").exists())

    def test_reconcile_command_reports_backlog_metrics(self):
        from .research_materialization import enqueue_session_materialization

        enqueue_session_materialization(self.session)
        output = []
        with patch("django.core.management.base.OutputWrapper.write", side_effect=output.append):
            call_command("reconcile_research_materializations", limit=10)

        payload = json.loads(next(item for item in output if str(item).startswith("{")))
        self.assertGreaterEqual(payload["backlog_count"], 1)
        self.assertIn("oldest_wait_seconds", payload)
        self.assertIn("failed_count", payload)

    def test_reconcile_skips_fresh_sessions_without_source_fingerprint(self):
        from .research_materialization import (
            enqueue_session_materialization,
            source_watermarks_for_session,
        )
        from .research_materialization_jobs import reconcile_session_materializations

        current = enqueue_session_materialization(self.session)
        current.status = current.Status.SUCCEEDED
        current.is_current = True
        current.finished_at = timezone.now()
        current.save(update_fields=["status", "is_current", "finished_at"])

        with patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            wraps=source_watermarks_for_session,
        ) as fingerprint:
            result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 0, "enqueued": 0})
        fingerprint.assert_not_called()

    def test_reconcile_detects_late_visit_outside_quick_event_markers(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations

        current = enqueue_session_materialization(self.session)
        current.status = current.Status.SUCCEEDED
        current.is_current = True
        current.finished_at = timezone.now()
        current.save(update_fields=["status", "is_current", "finished_at"])
        QuestionVisitEvent.objects.create(
            session=self.session,
            attempt=self.attempt,
            visit_index=1,
            event_sequence=1,
        )
        self.session.research_source_revision += 1
        self.session.save(update_fields=["research_source_revision"])

        result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 1, "enqueued": 1})

    def test_reconcile_limit_bounds_revision_candidates_without_fingerprints(self):
        from .research_materialization import (
            enqueue_session_materialization,
            source_watermarks_for_session,
        )
        from .research_materialization_jobs import reconcile_session_materializations

        sessions = [self.session, self._another_session(20), self._another_session(21)]
        for session in sessions:
            current = enqueue_session_materialization(session)
            current.status = current.Status.SUCCEEDED
            current.is_current = True
            current.finished_at = timezone.now()
            current.save(update_fields=["status", "is_current", "finished_at"])
            session.research_source_revision += 1
            session.save(update_fields=["research_source_revision"])

        with patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            wraps=source_watermarks_for_session,
        ) as fingerprint:
            result = reconcile_session_materializations(limit=2)

        self.assertEqual(result, {"scanned": 2, "enqueued": 2})
        fingerprint.assert_not_called()

    def test_reconcile_detects_late_grade_with_same_timestamp_and_higher_pk(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations

        submission = SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=uuid4(),
            final_answer="A",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        first_grade = GradeRecord.objects.create(
            submission=submission,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        )
        current = enqueue_session_materialization(self.session)
        current.status = current.Status.SUCCEEDED
        current.is_current = True
        current.finished_at = timezone.now()
        current.save(update_fields=["status", "is_current", "finished_at"])

        second_grade = GradeRecord.objects.create(
            submission=submission,
            version=2,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.CANDIDATE,
        )
        GradeRecord.objects.filter(pk=second_grade.pk).update(
            created_at=first_grade.created_at
        )
        self.session.research_source_revision += 1
        self.session.save(update_fields=["research_source_revision"])

        result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 1, "enqueued": 1})
        self.assertTrue(
            self.session.research_materializations.filter(status="queued").exists()
        )

    def test_reconcile_does_not_report_an_existing_active_rebuild_as_new(self):
        from .research_materialization import enqueue_session_materialization
        from .research_materialization_jobs import reconcile_session_materializations

        current = enqueue_session_materialization(self.session)
        current.status = current.Status.SUCCEEDED
        current.is_current = True
        current.projection_version = "old-version"
        current.save(update_fields=["status", "is_current", "projection_version"])
        queued = enqueue_session_materialization(self.session)

        result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 0, "enqueued": 0})
        self.assertEqual(
            self.session.research_materializations.filter(status="queued").get().pk,
            queued.pk,
        )

    def test_174_terminal_sessions_become_current_within_one_minute(self):
        from .research_materialization import (
            claim_session_materialization,
            run_session_materialization,
        )
        from .research_materialization_jobs import _mark_dirty_and_enqueue

        now = timezone.now()
        classes = [self.class_group]
        snapshots = [self.class_snapshot]
        for index in range(2, 5):
            class_group = ClassGroup.objects.create(
                course=self.course,
                name=f"物化性能班{index}",
                academic_year="2026",
            )
            classes.append(class_group)
            snapshots.append(
                ExperimentClassSnapshot.objects.create(
                    version=self.version,
                    source_class_group=class_group,
                    name_snapshot=class_group.name,
                    academic_year_snapshot=class_group.academic_year,
                    capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
                )
            )
        students = User.objects.bulk_create(
            [
                User(
                    username=f"materialization-scale-{index}",
                    role=User.Role.STUDENT,
                    student_number=f"MAT-SCALE-{index:03d}",
                )
                for index in range(1, 174)
            ]
        )
        CourseEnrollment.objects.bulk_create(
            [
                CourseEnrollment(
                    course=self.course,
                    class_group=classes[index % 4],
                    student=student,
                )
                for index, student in enumerate(students)
            ]
        )
        sessions = ExperimentSession.objects.bulk_create(
            [
                ExperimentSession(
                    version=self.version,
                    student=student,
                    class_snapshot=snapshots[index % 4],
                    class_snapshot_name=snapshots[index % 4].display_name,
                    status=ExperimentSession.Status.COMPLETED,
                    completed_at=now,
                    terminal_at=now,
                )
                for index, student in enumerate(students)
            ]
        )
        session_ids = [self.session.pk, *(session.pk for session in sessions)]

        started = time.monotonic()
        _mark_dirty_and_enqueue(session_ids)
        processed = 0
        while item := claim_session_materialization(
            worker_id="scale-worker", lease_seconds=90
        ):
            run_session_materialization(item, worker_id="scale-worker")
            processed += 1
        elapsed = time.monotonic() - started

        self.assertEqual(processed, 174)
        self.assertEqual(
            ResearchSessionMaterialization.objects.filter(
                session_id__in=session_ids,
                status=ResearchSessionMaterialization.Status.SUCCEEDED,
                is_current=True,
            ).count(),
            174,
        )
        self.assertLess(elapsed, 60)
