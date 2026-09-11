import json
from uuid import uuid4
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import (
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    SubmissionEvent,
)
from apps.accounts.models import CourseEnrollment, User


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

    def test_terminal_session_schedules_one_persistent_job_after_commit(self):
        from .research_materialization_jobs import schedule_session_materialization

        with self.captureOnCommitCallbacks(execute=True):
            schedule_session_materialization(self.session.pk)

        self.assertEqual(self.session.research_materializations.count(), 1)

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

    def test_reconcile_checks_fresh_sessions_with_complete_fingerprint(self):
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

        self.assertEqual(result, {"scanned": 1, "enqueued": 0})
        fingerprint.assert_called_once()

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

        result = reconcile_session_materializations(limit=10)

        self.assertEqual(result, {"scanned": 1, "enqueued": 1})

    def test_reconcile_limit_bounds_complete_fingerprint_scans(self):
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

        with patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            wraps=source_watermarks_for_session,
        ) as fingerprint:
            result = reconcile_session_materializations(limit=2)

        self.assertEqual(result, {"scanned": 2, "enqueued": 0})
        self.assertEqual(fingerprint.call_count, 2)

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
