from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import (
    ConfidenceAssignment,
    GradeRecord,
    QuestionVisitEvent,
    SubmissionEvent,
)
from apps.question_bank.models import Concept, QuestionConcept, QuestionOption

from .models import ResearchSessionMaterialization


class ResearchMaterializationModelTests(TestCase):
    def test_materialization_models_are_registered(self):
        self.assertIsNotNone(apps.get_model("analytics", "ResearchSessionMaterialization"))
        self.assertIsNotNone(apps.get_model("analytics", "ResearchSessionResponse"))
        self.assertIsNotNone(apps.get_model("analytics", "ResearchSessionVisit"))
        self.assertIsNotNone(apps.get_model("analytics", "ResearchSessionBehavior"))

    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def test_partial_unique_constraints_allow_history_but_not_duplicate_current_or_active(self):
        first = ResearchSessionMaterialization.objects.create(
            session=self.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        ResearchSessionMaterialization.objects.create(
            session=self.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResearchSessionMaterialization.objects.create(
                session=self.session,
                status=ResearchSessionMaterialization.Status.SUCCEEDED,
                is_current=True,
            )
        queued = ResearchSessionMaterialization.objects.create(session=self.session)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResearchSessionMaterialization.objects.create(
                session=self.session,
                status=ResearchSessionMaterialization.Status.RUNNING,
            )
        self.assertTrue(first.is_current)
        self.assertEqual(queued.status, ResearchSessionMaterialization.Status.QUEUED)

    def test_materialization_models_have_claim_and_materialization_first_indexes(self):
        materialization = ResearchSessionMaterialization._meta
        self.assertIsNotNone(materialization.get_field("source_high_watermarks"))
        self.assertIn(
            ("status", "lease_expires_at", "created_at"),
            {tuple(index.fields) for index in materialization.indexes},
        )
        expected = {
            "ResearchSessionResponse": ("materialization", "question_ref", "ordinal"),
            "ResearchSessionVisit": (
                "materialization", "question_ref", "visit_key", "ordinal",
            ),
            "ResearchSessionBehavior": (
                "materialization", "question_ref", "visit_key", "ordinal",
            ),
        }
        for model_name, fields in expected.items():
            with self.subTest(model=model_name):
                model = apps.get_model("analytics", model_name)
                self.assertIn(fields, {tuple(index.fields) for index in model._meta.indexes})


class ResearchMaterializationServiceTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def _services(self):
        from .research_materialization import (
            claim_session_materialization,
            enqueue_session_materialization,
            materialization_is_stale,
            run_session_materialization,
        )

        return (
            enqueue_session_materialization,
            claim_session_materialization,
            run_session_materialization,
            materialization_is_stale,
        )

    def _claim(self, worker="worker-1"):
        enqueue, claim, _run, _stale = self._services()
        enqueue(self.session)
        return claim(worker_id=worker, lease_seconds=60)

    @staticmethod
    def _bundle():
        return {
            "sessions": [{"session_ref": "S1", "question_count": 1, "behavior_count": 1}],
            "tables": {"sessions": (["sessionID"], [["S1"]])},
            "files": {"responses": [["response-row"]]},
            "responses": [
                {"question_ref": "Q001", "assigned_order": 1, "first_visit_order": 1}
            ],
            "visits": [],
            "behavior": [],
        }

    def test_enqueue_rejects_non_terminal_and_reuses_active_job(self):
        enqueue, _claim, _run, _stale = self._services()
        self.session.status = self.session.Status.IN_PROGRESS
        self.session.terminal_at = None
        self.session.save(update_fields=["status", "terminal_at"])
        with self.assertRaisesMessage(ValueError, "completed or expired"):
            enqueue(self.session)
        self.session.status = self.session.Status.EXPIRED
        self.session.terminal_at = timezone.now()
        self.session.save(update_fields=["status", "terminal_at"])
        first = enqueue(self.session)
        self.assertEqual(enqueue(self.session).pk, first.pk)

    def test_claim_sets_lease_and_recovers_expired_running_job(self):
        enqueue, claim, _run, _stale = self._services()
        now = timezone.now()
        enqueue(self.session)
        first = claim(worker_id="worker-1", now=now, lease_seconds=60)
        self.assertEqual(first.status, first.Status.RUNNING)
        self.assertEqual(first.locked_by, "worker-1")
        self.assertEqual(first.lease_expires_at, now + timedelta(seconds=60))
        self.assertIsNone(claim(worker_id="worker-2", now=now, lease_seconds=60))
        first.lease_expires_at = now - timedelta(seconds=1)
        first.save(update_fields=["lease_expires_at"])
        recovered = claim(worker_id="worker-2", now=now, lease_seconds=30)
        self.assertEqual(recovered.pk, first.pk)
        self.assertEqual(recovered.locked_by, "worker-2")
        self.assertEqual(recovered.retry_count, 1)

    def test_nullable_marker_ignores_rows_without_a_timestamp(self):
        from .research_materialization import _marker

        ConfidenceAssignment.objects.create(
            attempt=self.attempt,
            condition=ConfidenceAssignment.Condition.CONTROL,
            assignment_unit=ConfidenceAssignment.AssignmentUnit.ATTEMPT,
            assignment_version="marker-v1",
            response_status=ConfidenceAssignment.ResponseStatus.ASSIGNED,
        )
        self.assertEqual(
            _marker(ConfidenceAssignment.objects.all(), "committed_at"),
            ["", 0],
        )

    def test_non_postgresql_revision_fallback_detects_non_max_row_mutation(self):
        from .research_materialization import _database_revision

        first = QuestionVisitEvent.objects.create(
            session=self.session,
            attempt=self.attempt,
            visit_index=1,
            event_sequence=1,
        )
        QuestionVisitEvent.objects.create(
            session=self.session,
            attempt=self.attempt,
            visit_index=2,
            event_sequence=2,
        )
        queryset = QuestionVisitEvent.objects.filter(session=self.session)
        with patch(
            "apps.analytics.research_materialization.connection.vendor", "sqlite"
        ), patch(
            "apps.analytics.research_materialization.BitXor",
            side_effect=AssertionError("PostgreSQL aggregate used by fallback"),
        ):
            before = _database_revision(queryset)
            QuestionVisitEvent.objects.filter(pk=first.pk).update(
                entry_reason="fallback_update"
            )
            after = _database_revision(queryset)
        self.assertNotEqual(before, after)

    def test_expired_lease_cannot_be_renewed_published_or_recovered_past_retry_cap(self):
        from .research_materialization import (
            MAX_RETRIES,
            _store_success,
            renew_session_materialization_lease,
        )

        enqueue, claim, _run, _stale = self._services()
        now = timezone.now()
        item = enqueue(self.session)
        claim(worker_id="expired-worker", now=now, lease_seconds=1)
        expired_at = now + timedelta(seconds=2)

        self.assertIsNone(
            renew_session_materialization_lease(
                item, worker_id="expired-worker", now=expired_at, lease_seconds=60
            )
        )
        with self.assertRaisesMessage(ValueError, "lease"):
            _store_success(
                item.pk,
                worker_id="expired-worker",
                bundle=self._bundle(),
                cutoff=now,
                watermarks={},
                now=expired_at,
            )
        item.refresh_from_db()
        self.assertFalse(item.is_current)
        self.assertEqual(item.response_rows.count(), 0)

        item.retry_count = MAX_RETRIES - 1
        item.save(update_fields=["retry_count"])
        self.assertIsNone(claim(worker_id="replacement", now=expired_at, lease_seconds=60))
        item.refresh_from_db()
        self.assertEqual(item.status, item.Status.FAILED)
        self.assertEqual(item.retry_count, MAX_RETRIES)

    def test_lease_expiring_during_store_rolls_back_children_before_switch(self):
        from .research_materialization import _store_success, source_watermarks_for_session

        enqueue, claim, _run, _stale = self._services()
        started_at = timezone.now()
        item = enqueue(self.session)
        claim(worker_id="worker", now=started_at, lease_seconds=1)
        watermarks = source_watermarks_for_session(self.session)

        with patch(
            "apps.analytics.research_materialization.timezone.now",
            side_effect=(started_at, started_at + timedelta(seconds=2)),
        ), self.assertRaisesMessage(ValueError, "lease"):
            _store_success(
                item.pk,
                worker_id="worker",
                bundle=self._bundle(),
                cutoff=started_at,
                watermarks=watermarks,
            )

        item.refresh_from_db()
        self.assertFalse(item.is_current)
        self.assertEqual(item.response_rows.count(), 0)

    def test_claim_run_and_store_reject_invalidated_session_and_hide_old_current(self):
        from .research_materialization import _store_success

        enqueue, claim, run, stale = self._services()
        previous = ResearchSessionMaterialization.objects.create(
            session=self.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        queued = enqueue(self.session)
        type(self.session).objects.filter(pk=self.session.pk).update(
            status=self.session.Status.INVALIDATED
        )
        self.session.refresh_from_db()

        self.assertTrue(stale(previous))
        self.assertIsNone(claim(worker_id="worker"))
        queued.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(queued.status, queued.Status.FAILED)
        self.assertFalse(previous.is_current)

        type(self.session).objects.filter(pk=self.session.pk).update(
            status=self.session.Status.COMPLETED
        )
        running = enqueue(self.session)
        claim(worker_id="worker")
        type(self.session).objects.filter(pk=self.session.pk).update(
            status=self.session.Status.INVALIDATED
        )
        with patch(
            "apps.analytics.research_materialization.read_research_bundle",
            side_effect=AssertionError("invalidated session must not be read"),
        ):
            run(running, worker_id="worker")
        running.refresh_from_db()
        self.assertEqual(running.status, running.Status.FAILED)

        with self.assertRaisesMessage(ValueError, "invalidated"):
            _store_success(
                running.pk,
                worker_id="worker",
                bundle=self._bundle(),
                cutoff=timezone.now(),
                watermarks={},
            )

    def test_run_persists_bundle_rows_exactly_and_switches_current_atomically(self):
        enqueue, claim, run, _stale = self._services()
        cutoff = timezone.now()
        bundle = {
            "sessions": [{"session_ref": "S1", "question_count": 1, "behavior_count": 1}],
            "tables": {"sessions": (["sessionID"], [["S1"]])},
            "files": {"responses": [["response-row"]]},
            "responses": [{"question_ref": "Q001", "assigned_order": 1, "first_visit_order": 1}],
            "visits": [{"question_ref": "Q001", "key": "v1", "visit_index": 1, "entered_at": "t1", "row": ["visit-row"]}],
            "behavior": [{"question_ref": "Q001", "visit_key": "v1", "sequence": 1, "category": "题目访问", "action": "进入题目", "occurred_at": "t1", "row": ["behavior-row"]}],
        }
        first = enqueue(self.session)
        claim(worker_id="worker")
        with patch("apps.analytics.research_materialization.read_research_bundle", return_value=(bundle, cutoff)):
            run(first, worker_id="worker")
        first.refresh_from_db()
        self.assertTrue(first.is_current)
        self.assertEqual(first.session_summary, bundle["sessions"][0])
        self.assertEqual(first.session_row, ["S1"])
        self.assertEqual(first.row_counts, {"responses": 1, "visits": 1, "behavior": 1})
        self.assertEqual(first.response_rows.get().row, ["response-row"])
        self.assertEqual(first.response_rows.get().payload, bundle["responses"][0])
        self.assertEqual(first.visit_rows.get().row, ["visit-row"])
        self.assertEqual(first.behavior_rows.get().row, ["behavior-row"])
        self.assertEqual(len(first.quality_rows), 5)
        self.assertEqual(
            {row["question_code"] for row in first.quality_rows},
            {"Q001"},
        )
        self.assertEqual(
            {row["state"] for row in first.quality_rows},
            {"NOT_EVALUABLE", "NOT_APPLICABLE"},
        )
        second = enqueue(self.session)
        claim(worker_id="worker")
        with patch("apps.analytics.research_materialization.read_research_bundle", return_value=(bundle, cutoff)):
            run(second, worker_id="worker")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertIsNotNone(first.activated_at)
        self.assertEqual(first.superseded_at, second.activated_at)
        self.assertTrue(second.is_current)
        self.assertIsNotNone(second.activated_at)
        self.assertIsNone(second.superseded_at)
        self.assertEqual(self.session.research_materializations.filter(is_current=True).count(), 1)

    def test_failure_retries_and_preserves_previous_current(self):
        _enqueue, claim, run, _stale = self._services()
        previous = ResearchSessionMaterialization.objects.create(
            session=self.session, status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        current = self._claim()
        with patch("apps.analytics.research_materialization.read_research_bundle", side_effect=RuntimeError("boom")):
            for expected_retry, expected_status in ((1, "queued"), (2, "queued"), (3, "failed")):
                run(current, worker_id=f"worker-{expected_retry}")
                current.refresh_from_db()
                previous.refresh_from_db()
                self.assertEqual(current.retry_count, expected_retry)
                self.assertEqual(current.status, expected_status)
                self.assertFalse(current.is_current)
                self.assertTrue(previous.is_current)
                if expected_status == "queued":
                    current = claim(worker_id=f"worker-{expected_retry + 1}")

    def test_child_write_failure_rolls_back_before_current_switch(self):
        from .models import ResearchSessionBehavior

        enqueue, claim, run, _stale = self._services()
        previous = ResearchSessionMaterialization.objects.create(
            session=self.session, status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        item = enqueue(self.session)
        claim(worker_id="worker")
        bundle = {
            "sessions": [{"session_ref": "S1"}],
            "tables": {"sessions": (["sessionID"], [["S1"]])},
            "files": {"responses": [["response-row"]]},
            "responses": [{"question_ref": "Q001", "assigned_order": 1, "first_visit_order": None}],
            "visits": [],
            "behavior": [{"question_ref": "Q001", "row": ["behavior-row"]}],
        }
        with patch(
            "apps.analytics.research_materialization.read_research_bundle",
            return_value=(bundle, timezone.now()),
        ), patch.object(
            ResearchSessionBehavior.objects,
            "bulk_create",
            side_effect=RuntimeError("child write failed"),
        ):
            run(item, worker_id="worker")
        item.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(item.status, item.Status.QUEUED)
        self.assertEqual(item.response_rows.count(), 0)
        self.assertFalse(item.is_current)
        self.assertTrue(previous.is_current)

    def test_late_event_submission_and_grade_each_make_current_stale(self):
        from apps.assessment.event_services import append_assessment_event
        from apps.assessment.services import void_grade_record

        enqueue, claim, run, stale = self._services()

        def materialize():
            item = enqueue(self.session)
            claim(worker_id="worker")
            run(item, worker_id="worker")
            item.refresh_from_db()
            self.assertFalse(stale(item))
            return item

        first = materialize()
        append_assessment_event(
            event_type="window_blurred", event_version=1, event_domain="telemetry",
            actor_source="server", session=self.session, payload={},
        )
        self.assertTrue(stale(first))
        second = materialize()
        submission = SubmissionEvent.objects.create(
            attempt=self.attempt, submission_token=uuid4(),
            final_answer="答案", server_received_at=timezone.now(), committed_at=timezone.now(),
        )
        self.assertTrue(stale(second))
        third = materialize()
        grade = GradeRecord.objects.create(submission=submission, version=1)
        self.assertTrue(stale(third))
        fourth = materialize()
        void_grade_record(grade.pk, reason="研究物化水位测试")
        self.assertTrue(stale(fourth))

    def test_source_change_during_projection_retries_before_switch(self):
        from apps.assessment.event_services import append_assessment_event
        from .research_dataset import read_research_bundle as authoritative_read

        enqueue, claim, run, stale = self._services()
        item = enqueue(self.session)
        claim(worker_id="worker")
        calls = 0

        def read_with_late_event(scope, *, include_course_catalog=True):
            nonlocal calls
            calls += 1
            bundle, cutoff = authoritative_read(
                scope,
                include_course_catalog=include_course_catalog,
            )
            if calls == 1:
                append_assessment_event(
                    event_type="window_blurred", event_version=1,
                    event_domain="telemetry", actor_source="server",
                    session=self.session, attempt=self.attempt, payload={},
                )
            return bundle, cutoff

        with patch(
            "apps.analytics.research_materialization.read_research_bundle",
            side_effect=read_with_late_event,
        ):
            run(item, worker_id="worker")

        item.refresh_from_db()
        self.assertEqual(calls, 2)
        self.assertTrue(item.is_current)
        self.assertFalse(stale(item))
        self.assertEqual(item.row_counts["behavior"], 1)

    def test_source_change_at_final_store_check_rolls_back_and_rereads(self):
        enqueue, claim, run, _stale = self._services()
        item = enqueue(self.session)
        claim(worker_id="worker")
        stable = {"revision": "stable", "event": 0, "grade": {}}
        changed = {"revision": "changed", "event": 0, "grade": {}}
        revision_calls = 0

        def revision_sequence(_session, *, cutoff=None):
            nonlocal revision_calls
            revision_calls += 1
            return stable if revision_calls <= 3 else changed

        reader = patch(
            "apps.analytics.research_materialization.read_research_bundle",
            return_value=(self._bundle(), timezone.now()),
        )
        revisions = patch(
            "apps.analytics.research_materialization.source_watermarks_for_session",
            side_effect=revision_sequence,
        )
        with reader as read_mock, revisions:
            run(item, worker_id="worker")

        item.refresh_from_db()
        self.assertEqual(read_mock.call_count, 2)
        self.assertEqual(revision_calls, 8)
        self.assertTrue(item.is_current)
        self.assertEqual(item.source_high_watermarks, changed)
        self.assertEqual(item.response_rows.count(), 1)

    def test_mutating_each_per_session_source_family_makes_current_stale(self):
        enqueue, claim, run, stale = self._services()
        visit = QuestionVisitEvent.objects.create(
            session=self.session,
            attempt=self.attempt,
            visit_index=1,
            event_sequence=1,
        )
        submission = SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=uuid4(),
            final_answer="答案",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        grade = GradeRecord.objects.create(submission=submission, version=1)

        def materialize_then(mutation):
            item = enqueue(self.session)
            claim(worker_id="worker")
            run(item, worker_id="worker")
            item.refresh_from_db()
            self.assertFalse(stale(item))
            mutation()
            self.assertTrue(stale(item))

        mutations = (
            lambda: type(self.session).objects.filter(pk=self.session.pk).update(
                class_snapshot_name="更新后的班级"
            ),
            lambda: type(self.session.student).objects.filter(pk=self.session.student_id).update(
                display_name="更新后的姓名"
            ),
            lambda: type(self.attempt).objects.filter(pk=self.attempt.pk).update(
                status=self.attempt.Status.DRAFT
            ),
            lambda: QuestionVisitEvent.objects.filter(pk=visit.pk).update(
                entry_reason="keyboard_navigation"
            ),
            lambda: SubmissionEvent.objects.filter(pk=submission.pk).update(
                final_answer="修订答案"
            ),
            lambda: GradeRecord.objects.filter(pk=grade.pk).update(comment="复核备注"),
        )
        for mutation in mutations:
            materialize_then(mutation)

    def test_mutating_live_question_catalog_sources_does_not_make_current_stale(self):
        enqueue, claim, run, stale = self._services()
        question = self.attempt.assignment.snapshot.source_question
        identity = question.course_identity
        option = QuestionOption.objects.create(
            question=question, key="A", text="原选项", display_order=1
        )
        concept = Concept.objects.create(course=self.course, name="原知识点")
        link = QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
            display_order=1,
        )
        type(question).objects.filter(pk=question.pk).update(
            status=question.Status.DRAFT
        )
        question.status = question.Status.DRAFT

        def materialize_then(mutation):
            item = enqueue(self.session)
            claim(worker_id="worker")
            run(item, worker_id="worker")
            item.refresh_from_db()
            self.assertFalse(stale(item))
            mutation()
            self.assertFalse(stale(item))

        def update_link():
            link.display_order = 2
            link.save(update_fields=["display_order"])

        mutations = (
            lambda: type(question).objects.filter(pk=question.pk).update(text="更新题干"),
            lambda: type(identity).objects.filter(pk=identity.pk).update(course_sequence=2),
            lambda: QuestionOption.objects.filter(pk=option.pk).update(text="更新选项"),
            lambda: Concept.objects.filter(pk=concept.pk).update(name="更新知识点"),
            update_link,
        )
        for mutation in mutations:
            materialize_then(mutation)

    def test_per_session_bundle_skips_course_catalog_queries(self):
        from django.test.utils import CaptureQueriesContext

        from apps.accounts.models import CourseEnrollment
        from apps.question_bank.models import (
            Concept,
            CourseQuestionIdentity,
            Question,
            QuestionConcept,
            QuestionOption,
        )
        from .research_dataset import read_research_bundle
        from .research_materialization import _scope_for_session

        scope = _scope_for_session(self.session)
        blocked_tables = {
            model._meta.db_table.lower()
            for model in (
                CourseEnrollment,
                Question,
                CourseQuestionIdentity,
                QuestionOption,
                Concept,
                QuestionConcept,
            )
        }
        with patch.object(
            type(scope),
            "session_references",
            side_effect=AssertionError("per-session build queried all course sessions"),
        ), CaptureQueriesContext(connection) as captured_queries:
            bundle, _cutoff = read_research_bundle(
                scope,
                include_course_catalog=False,
            )

        sql = "\n".join(query["sql"].lower() for query in captured_queries)
        for table in blocked_tables:
            with self.subTest(table=table):
                self.assertNotIn(table, sql)
        snapshot = self.attempt.assignment.snapshot
        sequence = snapshot.course_sequence or snapshot.source_sequence
        expected_ref = (
            f"Q{sequence:03d}"
            if sequence
            else snapshot.code
        )
        self.assertEqual(
            {response["question_ref"] for response in bundle["responses"]},
            {expected_ref},
        )


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class ResearchMaterializationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    @staticmethod
    def _in_thread(barrier, callback):
        close_old_connections()
        try:
            if barrier is not None:
                barrier.wait(timeout=10)
            return callback()
        finally:
            connection.close()

    @staticmethod
    def _bundle():
        return ResearchMaterializationServiceTests._bundle()

    def test_postgresql_concurrent_enqueue_and_claim_have_single_winners(self):
        from .research_materialization import (
            claim_session_materialization,
            enqueue_session_materialization,
        )

        enqueue_barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._in_thread,
                    enqueue_barrier,
                    lambda: enqueue_session_materialization(
                        type(self.session).objects.get(pk=self.session.pk)
                    ).pk,
                )
                for _ in range(2)
            ]
            enqueued_ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(len(set(enqueued_ids)), 1)

        claim_barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self._in_thread,
                    claim_barrier,
                    lambda worker=worker: (
                        claim_session_materialization(worker_id=worker, lease_seconds=60)
                    ),
                )
                for worker in ("worker-1", "worker-2")
            ]
            claims = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_postgresql_takeover_prevents_expired_worker_current_switch(self):
        from .research_materialization import (
            _store_success,
            claim_session_materialization,
            enqueue_session_materialization,
            run_session_materialization,
            source_watermarks_for_session,
        )

        previous = ResearchSessionMaterialization.objects.create(
            session=self.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        item = enqueue_session_materialization(self.session)
        claimed = claim_session_materialization(
            worker_id="expired-worker", now=timezone.now(), lease_seconds=1
        )
        takeover_at = claimed.lease_expires_at + timedelta(seconds=1)
        marks = source_watermarks_for_session(self.session)
        cutoff = timezone.now()
        race_barrier = Barrier(2)

        def takeover_and_publish():
            replacement = claim_session_materialization(
                worker_id="replacement", now=takeover_at, lease_seconds=60
            )
            race_barrier.wait(timeout=10)
            return run_session_materialization(replacement, worker_id="replacement").pk

        def expired_publish():
            race_barrier.wait(timeout=10)
            try:
                _store_success(
                    item.pk,
                    worker_id="expired-worker",
                    bundle=self._bundle(),
                    cutoff=cutoff,
                    watermarks=marks,
                    now=takeover_at,
                )
            except ValueError as error:
                return str(error)
            return "published"

        close_old_connections()
        with patch(
            "apps.analytics.research_materialization.read_research_bundle",
            return_value=(self._bundle(), cutoff),
        ), ThreadPoolExecutor(max_workers=2) as pool:
            replacement_future = pool.submit(
                self._in_thread, None, takeover_and_publish
            )
            expired_future = pool.submit(self._in_thread, None, expired_publish)
            replacement_id = replacement_future.result(timeout=20)
            expired_result = expired_future.result(timeout=20)

        item.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(replacement_id, item.pk)
        self.assertIn("lease", expired_result)
        self.assertTrue(item.is_current)
        self.assertFalse(previous.is_current)

    def test_postgresql_final_switch_locks_identity_revision_until_commit(self):
        from apps.accounts.models import User
        from .models import ResearchSessionResponse
        from .research_materialization import (
            _store_success,
            claim_session_materialization,
            enqueue_session_materialization,
            materialization_is_stale,
            source_watermarks_for_session,
        )

        item = enqueue_session_materialization(self.session)
        claim_session_materialization(worker_id="worker", lease_seconds=60)
        marks = source_watermarks_for_session(self.session)
        cutoff = timezone.now()
        store_paused = Event()
        release_store = Event()
        update_started = Event()
        update_finished = Event()
        original_bulk_create = ResearchSessionResponse.objects.bulk_create

        def pause_after_children(rows, **kwargs):
            result = original_bulk_create(rows, **kwargs)
            store_paused.set()
            if not release_store.wait(timeout=10):
                raise TimeoutError("store release timed out")
            return result

        def publish():
            return _store_success(
                item.pk,
                worker_id="worker",
                bundle=self._bundle(),
                cutoff=cutoff,
                watermarks=marks,
            ).pk

        def update_identity():
            update_started.set()
            User.objects.filter(pk=self.session.student_id).update(display_name="并发改名")
            update_finished.set()

        close_old_connections()
        with patch.object(
            ResearchSessionResponse.objects,
            "bulk_create",
            side_effect=pause_after_children,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            publish_future = pool.submit(self._in_thread, None, publish)
            self.assertTrue(store_paused.wait(timeout=10))
            identity_future = pool.submit(self._in_thread, None, update_identity)
            self.assertTrue(update_started.wait(timeout=10))
            self.assertFalse(update_finished.wait(timeout=0.5))
            release_store.set()
            self.assertEqual(publish_future.result(timeout=20), item.pk)
            identity_future.result(timeout=20)
            self.assertTrue(update_finished.is_set())

        item.refresh_from_db()
        self.assertTrue(item.is_current)
        self.assertTrue(materialization_is_stale(item))

    def test_postgresql_final_switch_locks_visit_revision_until_commit(self):
        from .models import ResearchSessionResponse
        from .research_materialization import (
            _store_success,
            claim_session_materialization,
            enqueue_session_materialization,
            materialization_is_stale,
            source_watermarks_for_session,
        )

        visit = QuestionVisitEvent.objects.create(
            session=self.session,
            attempt=self.attempt,
            visit_index=1,
            event_sequence=1,
        )
        item = enqueue_session_materialization(self.session)
        claim_session_materialization(worker_id="worker", lease_seconds=60)
        marks = source_watermarks_for_session(self.session)
        cutoff = timezone.now()
        store_paused = Event()
        release_store = Event()
        update_started = Event()
        update_finished = Event()
        original_bulk_create = ResearchSessionResponse.objects.bulk_create

        def pause_after_children(rows, **kwargs):
            result = original_bulk_create(rows, **kwargs)
            store_paused.set()
            if not release_store.wait(timeout=10):
                raise TimeoutError("store release timed out")
            return result

        def publish():
            return _store_success(
                item.pk,
                worker_id="worker",
                bundle=self._bundle(),
                cutoff=cutoff,
                watermarks=marks,
            ).pk

        def update_visit():
            update_started.set()
            QuestionVisitEvent.objects.filter(pk=visit.pk).update(
                entry_reason="concurrent_update"
            )
            update_finished.set()

        close_old_connections()
        with patch.object(
            ResearchSessionResponse.objects,
            "bulk_create",
            side_effect=pause_after_children,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            publish_future = pool.submit(self._in_thread, None, publish)
            self.assertTrue(store_paused.wait(timeout=10))
            visit_future = pool.submit(self._in_thread, None, update_visit)
            self.assertTrue(update_started.wait(timeout=10))
            self.assertFalse(update_finished.wait(timeout=0.5))
            release_store.set()
            self.assertEqual(publish_future.result(timeout=20), item.pk)
            visit_future.result(timeout=20)
            self.assertTrue(update_finished.is_set())

        item.refresh_from_db()
        self.assertTrue(item.is_current)
        self.assertTrue(materialization_is_stale(item))
