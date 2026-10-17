from apps.api.test_support import domain_payload
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CourseEnrollment, User
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import ExperimentSession
from apps.experiments.models import ExperimentClassSnapshot, ExperimentVersion

from .models import ResearchSessionBehavior, ResearchSessionMaterialization, ResearchSessionQuality
from .research_materialization import PROJECTION_VERSION


@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class AdvancedMaterializedApiTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def _query(self, **values):
        return {
            "experiment_id": self.experiment.pk,
            "class_id": self.class_group.pk,
            **values,
        }

    def _materialize(self, session=None):
        from .research_materialization import (
            claim_session_materialization,
            enqueue_session_materialization,
            run_session_materialization,
        )

        item = enqueue_session_materialization(session or self.session)
        claimed = claim_session_materialization(worker_id="api-test", lease_seconds=900)
        self.assertEqual(claimed.pk, item.pk)
        run_session_materialization(claimed, worker_id="api-test")
        item.refresh_from_db()
        self.assertTrue(item.is_current, item.error)
        return item

    def test_pending_wait_starts_at_current_projection_queue_time(self):
        now = timezone.now()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            terminal_at=now - timedelta(days=26), completed_at=now - timedelta(days=26),
        )
        ResearchSessionMaterialization.objects.create(
            session=self.session, status="succeeded", is_current=True,
            projection_version="research-session-v3",
            source_revision=self.session.research_source_revision,
        )
        queued = ResearchSessionMaterialization.objects.create(
            session=self.session, projection_version=PROJECTION_VERSION,
            source_revision=self.session.research_source_revision,
        )
        ResearchSessionMaterialization.objects.filter(pk=queued.pk).update(
            created_at=now - timedelta(seconds=120),
        )
        with patch("apps.analytics.advanced_materialized.timezone.now", return_value=now):
            response = self.client.get(self.api("advanced/status/"), self._query())
        self.assertEqual(response.status_code, 200)
        preparation = domain_payload(response)["preparation"]
        self.assertEqual(preparation["pending_count"], 1)
        self.assertEqual(preparation["oldest_pending_seconds"], 120)
        self.assertIn("最早已等待 120 秒", preparation["message"])

    def test_missing_current_projection_job_does_not_claim_historical_wait(self):
        now = timezone.now()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            terminal_at=now - timedelta(days=26), completed_at=now - timedelta(days=26),
        )
        ResearchSessionMaterialization.objects.create(
            session=self.session, status="succeeded", is_current=True,
            projection_version="research-session-v3",
            source_revision=self.session.research_source_revision,
        )
        response = self.client.get(self.api("advanced/status/"), self._query())
        self.assertEqual(response.status_code, 200)
        preparation = domain_payload(response)["preparation"]
        self.assertEqual(preparation["pending_count"], 1)
        self.assertIsNone(preparation["oldest_pending_seconds"])
        self.assertNotIn("已等待", preparation["message"])
        self.assertIn("等待任务调度", preparation["message"])

    def _mark_ready(self, session):
        from .advanced_materialized import (
            _fallback_session_row,
            _fallback_session_summary,
        )

        from .research_materialization import PROJECTION_VERSION

        summary = _fallback_session_summary(session)
        summary["input_completeness"] = {"status": "complete"}
        now = timezone.now()
        return ResearchSessionMaterialization.objects.create(
            session=session,
            projection_version=PROJECTION_VERSION,
            source_revision=session.research_source_revision,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            finished_at=now,
            activated_at=now,
            session_summary=summary,
            session_row=_fallback_session_row(summary),
        )

    def test_readable_old_projection_is_not_latest_complete(self):
        current = self._mark_ready(self.session)
        current.session_summary["input_completeness"] = {"status": "complete"}
        current.save(update_fields=["session_summary"])
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            research_source_revision=current.source_revision + 1,
        )
        preparation = domain_payload(self.client.get(self.api("advanced/status/"), self._query()))["preparation"]
        self.assertEqual(preparation["ready_count"], 1)
        self.assertEqual(preparation["total_count"], 1)
        self.assertEqual(preparation["latest_complete_count"], 0)
        self.assertEqual(preparation["stale_count"], 1)

    def test_latest_complete_requires_current_revision_and_verified_capture(self):
        current = self._mark_ready(self.session)
        current.session_summary["input_completeness"] = {"status": "complete"}
        current.save(update_fields=["session_summary"])
        preparation = domain_payload(self.client.get(self.api("advanced/status/"), self._query()))["preparation"]
        self.assertEqual(preparation["latest_complete_count"], 1)
        self.assertEqual(preparation["input_incomplete_count"], 0)
        self.assertEqual(preparation["input_unverified_count"], 0)

    def test_missing_compact_coverage_is_unverified_instead_of_complete(self):
        current = self._mark_ready(self.session)
        current.session_summary.pop("input_completeness")
        current.save(update_fields=["session_summary"])
        preparation = domain_payload(self.client.get(self.api("advanced/status/"), self._query()))["preparation"]
        self.assertEqual(preparation["latest_complete_count"], 0)
        self.assertEqual(preparation["input_incomplete_count"], 1)
        self.assertEqual(preparation["input_unverified_count"], 1)
        self.assertEqual(preparation["state"], "incomplete")

    def test_fresh_projection_does_not_hide_incomplete_or_unverified_capture(self):
        current = self._mark_ready(self.session)
        for status, unverified in (("partial", 0), ("legacy_unknown", 1)):
            with self.subTest(status=status):
                current.session_summary["input_completeness"] = {"status": status}
                current.save(update_fields=["session_summary"])
                with CaptureQueriesContext(connection) as queries:
                    preparation = domain_payload(self.client.get(self.api("advanced/status/"), self._query()))["preparation"]
                self.assertEqual(preparation["state"], "incomplete")
                self.assertEqual(preparation["ready_count"], 1)
                self.assertEqual(preparation["latest_complete_count"], 0)
                self.assertEqual(preparation["input_incomplete_count"], 1)
                self.assertEqual(preparation["input_unverified_count"], unverified)
                sql = "\n".join(query["sql"] for query in queries.captured_queries)
                for table in ("assessment_assessmentevent", "assessment_clientcapturecheckpoint", "analytics_recordingcoveragesession"):
                    self.assertNotIn(table, sql)

    def test_materialization_records_existing_coverage_verdict_without_claiming_collection_complete(self):
        current = self._materialize()
        self.assertEqual(current.session_summary["input_completeness"]["status"], "legacy_unknown")
        preparation = domain_payload(self.client.get(self.api("advanced/status/"), self._query()))["preparation"]
        self.assertEqual(preparation["state"], "incomplete")
        self.assertEqual(preparation["latest_complete_count"], 0)

    def _complete_capture_with_unvisited_attempt(self):
        from apps.assessment.event_services import append_assessment_event
        from apps.assessment.models import Attempt, StudentQuestionAssignment
        from apps.assessment.recording_capture_services import ensure_capture_contract_v1, record_client_capture_checkpoint
        from apps.assessment.response_snapshot_services import create_response_snapshot
        from apps.assessment.time_sync_services import create_pending_time_sync, finalize_time_sync
        from apps.experiments.models import QuestionSnapshot
        from apps.experiments.presentation_instances import get_or_create_default_item_instance

        contract = ensure_capture_contract_v1()
        ExperimentSession.objects.filter(pk=self.session.pk).update(capture_contract=contract)
        self.session.refresh_from_db()
        now, client = timezone.now(), uuid4()
        sample = create_pending_time_sync(session=self.session, client_instance_id=client,
            client_wall_time_utc=now, client_time_origin=now, client_monotonic_ms=0)
        finalize_time_sync(time_sync_id=sample.pk, client_instance_id=client,
            client_received_wall_time_utc=now + timedelta(milliseconds=100), client_received_monotonic_ms=100)
        snapshot = create_response_snapshot(attempt=self.attempt, content="答案")
        events = [
            ("session_started", 2, "assessment", {"capture_contract_id": str(contract.pk), "capture_contract_sha256": contract.schema_sha256}, None),
            ("document_visible", 1, "telemetry", {}, None),
            ("window_focused", 1, "telemetry", {}, None),
            ("item_entered", 2, "assessment", {"entry_reason": "session_start", "from_assignment_id": None}, self.attempt),
            ("item_rendered", 1, "assessment", {"presentation_state": "interactive"}, self.attempt),
            ("response_state_changed", 1, "assessment", {"question_type": "short_answer", "response_state_before": None,
                "response_state_after": {"snapshot_id": str(snapshot.pk), "state_hash": snapshot.state_hash, "content_length": snapshot.content_length}, "change_source": "student"}, self.attempt),
            ("item_left", 1, "assessment", {"leave_reason": "finish"}, self.attempt),
            ("session_completed", 2, "assessment", {"completion_reason": "student", "terminal_at": now.isoformat()}, None),
        ]
        for sequence, (kind, version, domain, payload, attempt) in enumerate(events, 1):
            append_assessment_event(event_type=kind, event_version=version, event_domain=domain,
                actor_source="client", session=self.session, attempt=attempt, payload=payload,
                client_instance_id=client, client_sequence_no=sequence,
                client_wall_time_utc=now, client_time_origin=now, client_monotonic_ms=sequence * 100)
        record_client_capture_checkpoint(student=self.student, document={
            "checkpoint_id": str(uuid4()), "session_id": self.session.pk, "client_instance_id": str(client),
            "highest_emitted_sequence_no": len(events), "highest_acked_sequence_no": len(events),
            "highest_contiguous_acked_sequence_no": len(events), "pending_event_count": 0,
            "failed_event_count": 0, "missing_local_sequence_ranges": [], "client_monotonic_ms": "900",
            "client_wall_time_utc": now.isoformat(), "client_time_origin": now.isoformat(),
            "time_sync_id": str(sample.pk), "trigger": "navigation", "quality_flags": [], "app_version": "test",
        })
        self.attempt.first_opened_at = now
        self.attempt.visit_count = 1
        self.attempt.save(update_fields=["first_opened_at", "visit_count"])
        source = self.attempt.assignment.snapshot.source_question
        unseen_question = type(source).objects.create(bank=source.bank, sequence=2,
            question_type="short_answer", text="未访问题目", correct_answer="答案")
        unseen_snapshot = QuestionSnapshot.objects.create(version=self.version, source_question=unseen_question, source_sequence=2,
            code="UNSEEN", question_type="short_answer", text="未访问题目", correct_answer="答案")
        assignment = StudentQuestionAssignment.objects.create(session=self.session, snapshot=unseen_snapshot,
            item_instance=get_or_create_default_item_instance(unseen_snapshot), assigned_sequence_index=2)
        return Attempt.objects.create(session=self.session, assignment=assignment, status=Attempt.Status.UNANSWERED)

    def test_never_observed_unanswered_attempt_is_not_missing_expected_capture(self):
        unseen = self._complete_capture_with_unvisited_attempt()
        current = self._materialize()
        from .models import RecordingCoverageRun
        summary = current.session_summary["input_completeness"]
        coverage = RecordingCoverageRun.objects.get(pk=summary["coverage_run_id"])
        self.assertEqual(coverage.session_results.get().overall_status, "complete")
        self.assertEqual(coverage.attempt_results.get(attempt=unseen).overall_status, "insufficient")
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["required_attempt_count"], 1)
        self.assertEqual(summary["unobserved_attempt_count"], 1)

    def test_observed_unanswered_attempt_still_requires_its_capture(self):
        unseen = self._complete_capture_with_unvisited_attempt()
        unseen.first_opened_at = timezone.now()
        unseen.save(update_fields=["first_opened_at"])
        current = self._materialize()
        summary = current.session_summary["input_completeness"]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["required_attempt_count"], 2)
        self.assertEqual(summary["unobserved_attempt_count"], 0)

    def test_late_checkpoint_only_write_marks_projection_stale_and_reconciliation_recovers_queue_failure(self):
        from apps.assessment.recording_capture_services import record_client_capture_checkpoint
        from .research_materialization_jobs import reconcile_session_materializations

        current = self._materialize()
        now = timezone.now()
        document = {
            "checkpoint_id": str(uuid4()), "session_id": self.session.pk,
            "client_instance_id": str(uuid4()), "highest_emitted_sequence_no": 0,
            "highest_acked_sequence_no": 0, "highest_contiguous_acked_sequence_no": 0,
            "pending_event_count": 0, "failed_event_count": 0,
            "missing_local_sequence_ranges": [], "client_monotonic_ms": "0",
            "client_wall_time_utc": now.isoformat(), "client_time_origin": now.isoformat(),
            "time_sync_id": None, "trigger": "navigation", "quality_flags": [], "app_version": "test",
        }
        with patch("apps.analytics.research_materialization_jobs._enqueue_if_eligible", side_effect=RuntimeError("queue unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                record_client_capture_checkpoint(student=self.student, document=document)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, current.source_revision + 1)
        self.assertEqual(reconcile_session_materializations(limit=10)["enqueued"], 1)
        version = self.session.research_source_revision
        with self.captureOnCommitCallbacks(execute=True):
            record_client_capture_checkpoint(student=self.student, document=document)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, version)

    def test_session_time_sync_creation_and_finalization_refresh_projection_once_each(self):
        from apps.assessment.time_sync_services import create_pending_time_sync, finalize_time_sync

        current = self._materialize()
        now = timezone.now()
        client_id = uuid4()
        with self.captureOnCommitCallbacks(execute=True):
            sample = create_pending_time_sync(
                session=self.session, client_instance_id=client_id,
                client_wall_time_utc=now, client_time_origin=now,
                client_monotonic_ms=0,
            )
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, current.source_revision + 1)
        current = self._materialize()
        values = {
            "time_sync_id": sample.pk, "client_instance_id": client_id,
            "client_received_wall_time_utc": now + timedelta(milliseconds=100),
            "client_received_monotonic_ms": 100,
        }
        with self.captureOnCommitCallbacks(execute=True):
            finalize_time_sync(**values)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, current.source_revision + 1)
        version = self.session.research_source_revision
        with self.captureOnCommitCallbacks(execute=True):
            finalize_time_sync(**values)
        self.session.refresh_from_db()
        self.assertEqual(self.session.research_source_revision, version)

    def test_current_projection_query_is_lazy_and_filters_expected_version(self):
        from django.db.models import QuerySet
        from .advanced_materialized import _current_materializations
        from .research_materialization import PROJECTION_VERSION

        current = _current_materializations(ExperimentSession.objects.filter(pk=self.session.pk))
        self.assertIsInstance(current, QuerySet)
        self.assertIsNone(current._result_cache)
        self.assertIn(PROJECTION_VERSION, str(current.query))

    def test_advanced_status_is_authorized_metadata_without_preview_or_child_reads(self):
        self._mark_ready(self.session)
        with patch('apps.analytics.api.views.materialized_advanced_payload', side_effect=AssertionError('Status loaded page')):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.api('advanced/status/'), self._query())
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(set(domain_payload(response)), {'preparation'})
        self.assertEqual(domain_payload(response)['preparation']['state'], 'ready')
        for item in queries.captured_queries:
            for table in ('analytics_researchsessionresponse', 'analytics_researchsessionbehavior',
                          'analytics_researchsessionvisit', 'question_bank_question'):
                self.assertNotIn(table, item['sql'])
        self.client.logout()
        self.assertIn(self.client.get(self.api('advanced/status/'), self._query()).status_code, (401, 403))

    def test_status_version_changes_when_any_scoped_current_result_changes(self):
        first = self._mark_ready(self.session)
        before = domain_payload(self.client.get(self.api('advanced/status/'), self._query()))
        first.is_current = False
        first.save(update_fields=['is_current'])
        second = self._mark_ready(self.session)
        second.finished_at = first.finished_at
        second.save(update_fields=['finished_at'])
        after = domain_payload(self.client.get(self.api('advanced/status/'), self._query()))
        self.assertNotEqual(before['preparation']['result_version'], after['preparation']['result_version'])

    def test_failed_older_revision_does_not_stop_polling_changed_source(self):
        self._mark_ready(self.session)
        ResearchSessionMaterialization.objects.create(session=self.session, projection_version=PROJECTION_VERSION,
            source_revision=self.session.research_source_revision, status='failed', retry_count=3)
        ExperimentSession.objects.filter(pk=self.session.pk).update(research_source_revision=self.session.research_source_revision + 1)
        response = self.client.get(self.api('advanced/'), self._query(table='sessions'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['preparation']['state'], 'updating')

    def test_preview_does_not_select_bulk_materialization_json(self):
        self._materialize()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.api("advanced/"), self._query(table="responses"))
        self.assertEqual(response.status_code, 200)
        parent_selects = [query["sql"].split(" FROM ")[0] for query in queries.captured_queries
                          if query["sql"].startswith("SELECT")]
        for sql in parent_selects:
            for field in ("quality_rows", "source_high_watermarks", "grade_high_watermark"):
                self.assertNotIn(f'"analytics_researchsessionmaterialization"."{field}"', sql)

    def test_source_revision_mismatch_marks_compatible_rows_updating_without_job(self):
        current = self._materialize()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            research_source_revision=current.source_revision + 1
        )
        response = self.client.get(self.api("advanced/"), self._query(table="responses"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["preparation"]["state"], "updating")
        self.assertTrue(domain_payload(response)["preparation"]["stale"])
        self.assertEqual(domain_payload(response)["preparation"]["ready_count"], 1)
        self.assertEqual(domain_payload(response)["preview"]["page"]["total_items"], 1)

    def test_incompatible_projection_is_pending_and_never_served(self):
        current = self._materialize()
        current.projection_version = "unexpected-version"
        current.save(update_fields=["projection_version"])
        payload = domain_payload(self.client.get(self.api("advanced/"), self._query(table="responses")))
        self.assertEqual(payload["preview"]["rows"], [])
        self.assertEqual(payload["preparation"]["state"], "preparing")
        self.assertEqual(payload["preparation"]["pending_count"], 1)

    def test_quality_reads_twenty_normalized_rows_and_counts_in_sql(self):
        current = self._materialize()
        original_count = current.quality_entries.count()
        ResearchSessionQuality.objects.bulk_create([
            ResearchSessionQuality(materialization=current, ordinal=100 + ordinal,
                state="PASS", rule="page-test", question_ref="Q001", payload={})
            for ordinal in range(25)
        ])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.api("quality/"), self._query())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(domain_payload(response)["items"]), 20)
        self.assertEqual(domain_payload(response)["page"]["total_items"], original_count + 25)
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertIn("LIMIT 20", sql)
        self.assertNotIn('"analytics_researchsessionmaterialization"."quality_rows"', sql)
        self.assertNotIn('"analytics_researchsessionmaterialization"."row_counts"', sql)

    def test_capture_priority_retry_hint_keeps_authorized_cached_rows_and_counts(self):
        current = self._materialize()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            research_source_revision=current.source_revision + 1
        )
        baseline = domain_payload(self.client.get(self.api("advanced/"), self._query(table="responses")))
        with patch("apps.analytics.advanced_materialized.runtime_pressure", create=True,
                   return_value={"mode": "capture_priority", "expires_at": 123, "retry_after_seconds": 15}):
            payload = domain_payload(self.client.get(self.api("advanced/"), self._query(table="responses")))
        self.assertEqual(payload["preparation"]["retry_after_seconds"], 15)
        self.assertEqual(payload["preparation"]["state"], "updating")
        self.assertEqual(payload["preview"]["rows"], baseline["preview"]["rows"])
        self.assertEqual(payload["tables"], baseline["tables"])
        for field in ("ready_count", "pending_count", "failed_count", "updating_count"):
            self.assertEqual(payload["preparation"][field], baseline["preparation"][field])

    def test_in_progress_session_is_not_exposed(self):
        self.session.status = ExperimentSession.Status.IN_PROGRESS
        self.session.completed_at = None
        self.session.terminal_at = None
        self.session.save(update_fields=["status", "completed_at", "terminal_at"])

        response = self.client.get(self.api("advanced/"), self._query(table="sessions"))

        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["selected_table"]["total_items"], 0)
        self.assertEqual(payload["preview"]["page"]["total_items"], 0)
        self.assertEqual(payload["preview"]["rows"], [])
        self.assertEqual(payload["workspace"]["sessions"], [])
        self.assertEqual(payload["preparation"]["pending_count"], 0)

        quality = domain_payload(self.client.get(self.api("quality/"), self._query()))
        self.assertEqual(quality["page"]["total_items"], 0)
        self.assertEqual(quality["preparation"]["pending_count"], 0)

    def test_terminal_session_waiting_for_projection_is_anonymous(self):
        response = self.client.get(self.api("advanced/"), self._query(table="sessions"))

        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["selected_table"]["total_items"], 0)
        self.assertEqual(payload["preview"]["page"]["total_items"], 0)
        self.assertEqual(payload["preview"]["rows"], [])
        self.assertEqual(payload["workspace"]["sessions"], [])
        self.assertIsNone(payload["workspace"]["session"])
        self.assertEqual(payload["preparation"]["state"], "preparing")
        self.assertEqual(payload["preparation"]["pending_count"], 1)
        self.assertIsNone(payload["preparation"]["oldest_pending_seconds"])
        self.assertNotIn("已等待", payload["preparation"]["message"])
        self.assertNotIn(self.student.student_number, payload["preparation"]["message"])

        quality = domain_payload(self.client.get(self.api("quality/"), self._query()))
        self.assertEqual(quality["page"]["total_items"], 0)
        self.assertEqual(quality["items"], [])
        self.assertEqual(quality["preparation"]["pending_count"], 1)

        student_view = domain_payload(self.client.get(
            self.api("advanced/"),
            self._query(view="student", student_id=self.student.pk),
        ))
        self.assertIsNone(student_view["workspace"]["student"])

    def test_selected_table_reads_materialized_rows_without_live_bundle(self):
        item = self._materialize()

        with CaptureQueriesContext(connection) as queries:
            with patch(
                "apps.analytics.api.views.read_research_bundle",
                side_effect=AssertionError("legacy full bundle called"),
            ):
                response = self.client.get(
                    self.api("advanced/"), self._query(table="responses")
                )

        self.assertEqual(response.status_code, 200, response.content[:300])
        payload = domain_payload(response)
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        self.assertEqual(payload["preparation"]["state"], "incomplete")
        version = payload['preparation']['result_version']
        self.assertRegex(version, r'^[0-9a-f]{32}$')
        metadata = domain_payload(self.client.get(self.api('advanced/status/'), self._query()))
        self.assertEqual(metadata['preparation']['result_version'], version)
        other_table = domain_payload(self.client.get(self.api('advanced/'), self._query(table='sessions')))
        self.assertEqual(other_table['preparation']['result_version'], version)
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertLess(len(queries), 40)
        self.assertNotIn('"analytics_researchsessionvisit"', sql)
        self.assertNotIn('"analytics_researchsessionbehavior"', sql)

    def test_quality_reads_materialized_rows_without_live_research_scans(self):
        self._materialize()

        with patch(
            "apps.analytics.api.views.build_quality_report",
            side_effect=AssertionError("live quality builder called"),
        ), patch(
            "apps.analytics.api.views.read_research_dataset",
            side_effect=AssertionError("live research dataset called"),
        ):
            response = self.client.get(self.api("quality/"), self._query())

        self.assertEqual(response.status_code, 200, response.content[:300])
        payload = domain_payload(response)
        self.assertEqual(payload["page"]["total_items"], 5)
        self.assertEqual(payload["preparation"]["state"], "incomplete")
        self.assertEqual(payload["preparation"]["ready_count"], 1)
        self.assertEqual({item["question_code"] for item in payload["items"]}, {"Q001"})

    def test_failed_refresh_keeps_current_rows_and_reports_anonymous_failure(self):
        current = self._materialize()
        before = domain_payload(self.client.get(self.api('advanced/status/'), self._query()))['preparation']['result_version']
        ResearchSessionMaterialization.objects.create(
            session=self.session,
            projection_version=PROJECTION_VERSION,
            status=ResearchSessionMaterialization.Status.FAILED,
            finished_at=timezone.now(),
            error="refresh failed",
        )

        response = self.client.get(self.api("advanced/"), self._query(table="sessions"))

        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        self.assertEqual(payload["preparation"]["state"], "failed")
        self.assertEqual(payload["preparation"]["ready_count"], 1)
        self.assertEqual(payload["preparation"]["failed_count"], 1)
        self.assertEqual(payload['preparation']['result_version'], before)
        self.assertEqual(payload['preview']['rows'], [current.session_row])
        metadata = domain_payload(self.client.get(self.api('advanced/status/'), self._query()))
        self.assertEqual(metadata['preparation']['result_version'], before)
        self.assertEqual(metadata['preparation']['state'], 'failed')

    def test_question_table_does_not_load_behavior_child_rows(self):
        self._materialize()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                self.api("advanced/"), self._query(table="questions")
            )

        self.assertEqual(response.status_code, 200)
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertLess(len(queries), 40)
        self.assertNotIn('"analytics_researchsessionbehavior"', sql)
        self.assertEqual(domain_payload(response)["selected_table"]["key"], "questions")

    def test_standard_page_clamps_rows_and_metadata_together(self):
        self._materialize()

        response = self.client.get(
            self.api("advanced/"), self._query(table="responses", page=999)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["preview"]["page"]["number"], 1)
        self.assertEqual(len(domain_payload(response)["preview"]["rows"]), 1)

    def test_behavior_uses_fixed_result_cursor_and_twenty_row_limit(self):
        item = self._materialize()
        ResearchSessionBehavior.objects.bulk_create(
            [
                ResearchSessionBehavior(
                    materialization=item,
                    ordinal=ordinal,
                    question_ref="Q001",
                    visit_key="Q001:1",
                    sequence=ordinal,
                    category="测试",
                    action="行为",
                    occurred_at=f"t{ordinal}",
                    row=[f"event-{ordinal}"],
                    payload={"question_ref": "Q001", "sequence": ordinal},
                )
                for ordinal in range(1, 26)
            ]
        )
        item.row_counts = {**item.row_counts, "behavior": 25}
        item.save(update_fields=["row_counts"])
        later_result = ResearchSessionMaterialization.objects.create(session=self.session)
        ResearchSessionBehavior.objects.create(
            materialization=later_result,
            ordinal=1,
            question_ref="Q001",
            visit_key="Q001:1",
            sequence=999,
            category="测试",
            action="稍后发布",
            occurred_at="later",
            row=["later-result"],
            payload={"question_ref": "Q001", "sequence": 999},
        )

        first = domain_payload(self.client.get(
            self.api("advanced/"), self._query(table="behavior_events")
        ))
        self.assertEqual(len(first["preview"]["rows"]), 20)
        self.assertIsNone(first["preview"]["cursor"]["previous"])
        next_cursor = first["preview"]["cursor"]["next"]
        self.assertTrue(next_cursor)

        phantom_student = User.objects.create_user(
            username="behavior-cursor-phantom",
            role=User.Role.STUDENT,
            student_number="CURSOR-PHANTOM",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=phantom_student,
        )
        phantom_session = ExperimentSession.objects.create(
            version=self.version,
            student=phantom_student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        phantom_result = ResearchSessionMaterialization.objects.create(
            session=phantom_session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            activated_at=timezone.now(),
            finished_at=timezone.now(),
            row_counts={"behavior": 1},
        )
        ResearchSessionBehavior.objects.create(
            materialization=phantom_result,
            ordinal=1,
            sequence=1,
            row=["phantom-session"],
            payload={"sequence": 1},
        )

        later_result.status = later_result.Status.SUCCEEDED
        later_result.finished_at = timezone.now()
        later_result.save(update_fields=["status", "finished_at"])

        second = self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", cursor=next_cursor),
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(domain_payload(second)["preview"]["rows"]), 5)
        self.assertNotIn(["later-result"], domain_payload(second)["preview"]["rows"])
        self.assertNotIn(["phantom-session"], domain_payload(second)["preview"]["rows"])
        self.assertEqual(
            domain_payload(second)["preview"]["cursor"]["result_version"],
            first["preview"]["cursor"]["result_version"],
        )
        fresh = self.client.get(
            self.api("advanced/"), self._query(table="behavior_events")
        )
        self.assertNotIn(["later-result"], domain_payload(fresh)["preview"]["rows"])
        rejected = self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", cursor=next_cursor + "tampered"),
        )
        self.assertEqual(rejected.status_code, 400)

    def test_behavior_first_page_uses_constant_publication_barrier(self):
        self._materialize()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.api("advanced/"), self._query(table="behavior_events"))
        self.assertEqual(response.status_code, 200)
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertIn("pg_advisory_xact_lock", sql)
        self.assertNotIn("FOR UPDATE", sql)

    def test_behavior_cursor_reports_stable_page_number_for_sparse_ordinals(self):
        item = self._materialize()
        ResearchSessionBehavior.objects.bulk_create(
            [
                ResearchSessionBehavior(
                    materialization=item,
                    ordinal=ordinal,
                    question_ref="QSPARSE",
                    visit_key="QSPARSE:1",
                    sequence=ordinal,
                    row=[f"event-{ordinal}"],
                    payload={"question_ref": "QSPARSE", "sequence": ordinal},
                )
                for ordinal in range(101, 126)
            ]
        )

        first = domain_payload(self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", question_ref="QSPARSE"),
        ))
        second = domain_payload(self.client.get(
            self.api("advanced/"),
            self._query(
                table="behavior_events",
                question_ref="QSPARSE",
                cursor=first["preview"]["cursor"]["next"],
            ),
        ))

        self.assertEqual(first["preview"]["page"]["number"], 1)
        self.assertEqual(second["preview"]["page"]["number"], 2)

    def test_student_search_is_server_limited_and_session_options_are_scoped(self):
        self._materialize()
        for index in range(25):
            student = User.objects.create_user(
                username=f"search-student-{index:02d}",
                role=User.Role.STUDENT,
                student_number=f"SEARCH-{index:02d}",
                display_name=f"检索学生 {index:02d}",
            )
            CourseEnrollment.objects.create(
                course=self.course, class_group=self.class_group, student=student
            )
            session = ExperimentSession.objects.create(
                version=self.version,
                student=student,
                class_snapshot=self.class_snapshot,
                class_snapshot_name=self.class_snapshot.display_name,
                status=ExperimentSession.Status.COMPLETED,
            )
            self._mark_ready(session)

        search = self.client.get(
            self.api("advanced/students/"), self._query(q="检索学生")
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(len(domain_payload(search)["items"]), 20)

        response = self.client.get(
            self.api("advanced/"),
            self._query(view="student", student_id=self.student.pk),
        )
        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(
            {row["student_id"] for row in payload["workspace"]["sessions"]},
            {self.student.pk},
        )
        self.assertLessEqual(len(payload["session_options"]), 20)

    def test_student_view_keeps_all_student_sessions_when_one_session_is_selected(self):
        from .advanced_materialized import _session_reference

        self._materialize()

        version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=self.version.version_number + 1,
            published_by=self.teacher,
        )
        class_snapshot = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        other = ExperimentSession.objects.create(
            version=version,
            student=self.student,
            class_snapshot=class_snapshot,
            class_snapshot_name=class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        self._mark_ready(other)

        response = self.client.get(
            self.api("advanced/"),
            self._query(
                view="student",
                student_id=self.student.pk,
                session_ref=_session_reference(self.session),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["session_ref"] for row in domain_payload(response)["workspace"]["sessions"]},
            {_session_reference(self.session), _session_reference(other)},
        )

    def test_student_view_keeps_an_explicit_session_older_than_display_limit(self):
        from .advanced_materialized import _session_reference

        self._materialize()

        terminal_at = timezone.now()
        versions = ExperimentVersion.objects.bulk_create(
            [
                ExperimentVersion(
                    experiment=self.experiment,
                    version_number=self.version.version_number + index + 1,
                    published_by=self.teacher,
                )
                for index in range(51)
            ]
        )
        snapshots = ExperimentClassSnapshot.objects.bulk_create(
            [
                ExperimentClassSnapshot(
                    version=version,
                    source_class_group=self.class_group,
                    name_snapshot=self.class_group.name,
                    academic_year_snapshot=self.class_group.academic_year,
                    capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
                )
                for version in versions
            ]
        )
        sessions = ExperimentSession.objects.bulk_create(
            [
                ExperimentSession(
                    version=version,
                    student=self.student,
                    class_snapshot=class_snapshot,
                    class_snapshot_name=class_snapshot.display_name,
                    status=ExperimentSession.Status.COMPLETED,
                    completed_at=terminal_at,
                    terminal_at=terminal_at,
                )
                for version, class_snapshot in zip(versions, snapshots)
            ]
        )
        for session in sessions:
            self._mark_ready(session)

        response = self.client.get(
            self.api("advanced/"),
            self._query(
                view="student",
                student_id=self.student.pk,
                session_ref=_session_reference(self.session),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            domain_payload(response)["workspace"]["session"]["session_ref"],
            _session_reference(self.session),
        )
        self.assertIn(
            _session_reference(self.session),
            [item["session_ref"] for item in domain_payload(response)["workspace"]["sessions"]],
        )
