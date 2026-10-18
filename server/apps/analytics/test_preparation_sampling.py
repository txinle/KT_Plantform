from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.dateparse import parse_datetime

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import ExperimentSession

from .advanced_materialized import materialized_advanced_status
from .data_scope import DataScope
from .models import ResearchSessionMaterialization
from .research_materialization import PROJECTION_VERSION, _store_failure, enqueue_session_materialization


@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class PreparationSamplingTests(TestCase):
    checked_at = datetime(2026, 10, 18, 12, 5, tzinfo=datetime_timezone.utc)

    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session
        self.scope = DataScope.from_mapping(self.course, {
            "experiment_id": self.experiment.pk, "class_id": self.class_group.pk,
        })

    def _job(self, *, age=300, session=None, **values):
        session = session or self.session
        values = {
            "source_revision": session.research_source_revision,
            "projection_version": PROJECTION_VERSION,
            **values,
        }
        job = ResearchSessionMaterialization.objects.create(session=session, **values)
        ResearchSessionMaterialization.objects.filter(pk=job.pk).update(created_at=self.checked_at - timedelta(seconds=age))
        job.refresh_from_db()
        return job

    def _another_session(self):
        student = User.objects.create_user(username="sampling-second", role=User.Role.STUDENT)
        return ExperimentSession.objects.create(
            version=self.version, student=student,
            class_snapshot=self.class_snapshot, class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )

    def _sample(self):
        with patch("apps.analytics.advanced_materialized.timezone.now", return_value=self.checked_at) as clock:
            with CaptureQueriesContext(connection) as queries:
                preparation = materialized_advanced_status(self.scope, {})["preparation"]
        clock.assert_called_once_with()
        self.assertEqual(len(queries), 2)
        sql = "\n".join(item["sql"] for item in queries)
        for table in ("analytics_researchsessionbehavior", "analytics_researchsessionresponse", "analytics_researchsessionvisit", "assessment_assessmentevent"):
            self.assertNotIn(table, sql)
        self.assertEqual(preparation["status_checked_at"], self.checked_at)
        return preparation

    def test_job_age_includes_queue_and_processing_and_uses_one_sample(self):
        self._job(
            started_at=self.checked_at - timedelta(seconds=180),
            status="running", locked_by="sampling-worker",
            lease_expires_at=self.checked_at + timedelta(seconds=90),
        )
        preparation = self._sample()
        self.assertEqual(preparation["oldest_pending_seconds"], 300)
        self.assertIsNone(preparation["updated_at"])
        self.assertIn("包含排队、处理和重试", preparation["message"])

    def test_automatic_retry_retains_original_job_age(self):
        job = self._job(
            status="running", locked_by="sampling-worker",
            started_at=self.checked_at - timedelta(seconds=180),
            lease_expires_at=self.checked_at + timedelta(seconds=90),
        )
        with patch("apps.analytics.research_materialization.timezone.now", return_value=self.checked_at):
            _store_failure(job.pk, worker_id="sampling-worker", error=RuntimeError("temporary"))
        preparation = self._sample()
        self.assertEqual(preparation["oldest_pending_seconds"], 300)
        self.assertEqual(preparation["pending_count"], 1)

    def test_forced_retry_measures_new_job_without_old_failed_age(self):
        self._job(age=3600, status="failed")
        retry_at = self.checked_at - timedelta(seconds=60)
        with patch("apps.analytics.research_materialization.timezone.now", return_value=retry_at):
            retry = enqueue_session_materialization(self.session, force_retry=True)
        self.assertEqual(retry.created_at, retry_at)
        preparation = self._sample()
        self.assertEqual(preparation["state"], "preparing")
        self.assertEqual(preparation["failed_count"], 0)
        self.assertEqual(preparation["oldest_pending_seconds"], 60)

    def test_old_projection_and_source_revision_do_not_supply_pending_age(self):
        self._job(age=7200, status="failed", projection_version="research-session-v0")
        old_revision = self.session.research_source_revision
        ExperimentSession.objects.filter(pk=self.session.pk).update(research_source_revision=old_revision + 1)
        self.session.refresh_from_db()
        self._job(age=3600, status="failed", source_revision=old_revision)
        self._job(age=120)
        preparation = self._sample()
        self.assertEqual(preparation["oldest_pending_seconds"], 120)
        self.assertEqual(preparation["pending_count"], 1)
        self.assertEqual(preparation["failed_count"], 0)

    def test_mixed_failure_does_not_replace_pending_age_with_failed_age(self):
        self._job(age=7200, status="failed")
        self._job(age=300, session=self._another_session())
        preparation = self._sample()
        self.assertEqual(preparation["state"], "failed")
        self.assertEqual((preparation["failed_count"], preparation["pending_count"]), (1, 1))
        self.assertEqual(preparation["oldest_pending_seconds"], 300)

    def test_updating_readable_version_is_not_missing_initial_result(self):
        finished_at = self.checked_at - timedelta(seconds=240)
        self._job(status="succeeded", is_current=True, finished_at=finished_at)
        ExperimentSession.objects.filter(pk=self.session.pk).update(research_source_revision=self.session.research_source_revision + 1)
        self.session.refresh_from_db()
        self._job(age=120)
        preparation = self._sample()
        self.assertEqual(preparation["state"], "updating")
        self.assertEqual((preparation["ready_count"], preparation["updating_count"]), (1, 1))
        self.assertEqual(preparation["pending_count"], 0)
        self.assertIsNone(preparation["oldest_pending_seconds"])
        self.assertEqual(preparation["updated_at"], finished_at)

    def test_missing_job_reports_unknown_age(self):
        preparation = self._sample()
        self.assertEqual(preparation["pending_count"], 1)
        self.assertIsNone(preparation["oldest_pending_seconds"])
        self.assertIn("等待任务调度", preparation["message"])

    def test_oldest_completion_moves_age_to_remaining_pending_job(self):
        oldest = self._job(age=600)
        self._job(age=300, session=self._another_session())
        self.assertEqual(self._sample()["oldest_pending_seconds"], 600)
        ResearchSessionMaterialization.objects.filter(pk=oldest.pk).update(
            status="succeeded", is_current=True, finished_at=self.checked_at,
        )
        preparation = self._sample()
        self.assertEqual(preparation["oldest_pending_seconds"], 300)
        self.assertEqual((preparation["ready_count"], preparation["pending_count"]), (1, 1))

    def test_status_api_preserves_sample_and_success_times_separately(self):
        self._job()
        with patch("apps.analytics.advanced_materialized.timezone.now", return_value=self.checked_at):
            response = self.client.get(
                f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/status/",
                {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
            )
        self.assertEqual(response.status_code, 200)
        preparation = domain_payload(response)["preparation"]
        self.assertEqual(parse_datetime(preparation["status_checked_at"]), self.checked_at)
        self.assertEqual(preparation["oldest_pending_seconds"], 300)
        self.assertIsNone(preparation["updated_at"])
