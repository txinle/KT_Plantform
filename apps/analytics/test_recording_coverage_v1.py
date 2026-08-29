import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.assessment.event_services import append_assessment_event
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    SubmissionEvent,
    StudentQuestionAssignment,
)
from apps.assessment.response_snapshot_services import create_response_snapshot
from apps.assessment.recording_capture_services import (
    ensure_capture_contract_v1,
    record_client_capture_checkpoint,
)
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .models import RecordingCoverageRun
from .recording_coverage import _derived_features, generate_session_coverage


class RecordingCoverageV1Tests(TestCase):
    def setUp(self):
        teacher = User.objects.create_user(
            username="coverage-teacher", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="COVERAGE-S1",
            student_number="COVERAGE-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=teacher, name="Coverage 题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="Coverage 题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=teacher, question_bank=bank, name="Coverage 实验"
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment, version_number=1, published_by=teacher
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="Coverage 题目",
            options=[{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
            correct_answer="A",
            primary_kc="KC",
            all_kcs=["KC"],
        )
        item_instance = get_or_create_default_item_instance(snapshot)
        self.session = ExperimentSession.objects.create(
            version=version,
            student=self.student,
            capture_contract=ensure_capture_contract_v1(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=self.session,
            snapshot=snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(
            session=self.session, assignment=assignment
        )
        self.client_instance_id = uuid.uuid4()
        self.origin = timezone.now()

    def append(
        self, event_type, sequence, payload, *, client_instance_id=None,
        client_time_origin=None
    ):
        return append_assessment_event(
            event_type=event_type,
            event_version=1,
            event_domain="telemetry" if event_type.startswith("document_") else "assessment",
            actor_source="client",
            session=self.session,
            attempt=self.attempt,
            payload=payload,
            client_instance_id=client_instance_id or self.client_instance_id,
            client_sequence_no=sequence,
            client_wall_time_utc=self.origin,
            client_monotonic_ms=Decimal(sequence * 100),
            client_time_origin=client_time_origin or self.origin,
        )

    def append_server(self, event_type, payload):
        return append_assessment_event(
            event_type=event_type,
            event_version=2,
            event_domain="assessment",
            actor_source="server",
            session=self.session,
            assignment=self.attempt.assignment,
            attempt=self.attempt,
            payload=payload,
            app_version="test-server",
        )

    def checkpoint(self, *, emitted, acked, contiguous, pending=0):
        return record_client_capture_checkpoint(
            student=self.student,
            document={
                "checkpoint_id": str(uuid.uuid4()),
                "session_id": self.session.pk,
                "client_instance_id": str(self.client_instance_id),
                "highest_emitted_sequence_no": emitted,
                "highest_acked_sequence_no": acked,
                "highest_contiguous_acked_sequence_no": contiguous,
                "pending_event_count": pending,
                "failed_event_count": 0,
                "missing_local_sequence_ranges": [],
                "client_monotonic_ms": str(emitted * 100),
                "client_wall_time_utc": self.origin.isoformat(),
                "client_time_origin": self.origin.isoformat(),
                "time_sync_id": None,
                "trigger": "navigation",
                "quality_flags": ["clock_unsynchronized"],
                "app_version": "test",
            },
        )

    def test_internal_sequence_gap_is_materialized_as_partial(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.append(
            "response_state_changed",
            2,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
        )
        self.append("item_left", 4, {"leave_reason": "navigation"})
        self.checkpoint(emitted=4, acked=4, contiguous=2)

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)

        self.assertEqual(result.overall_status, "partial")
        self.assertEqual(result.sequence_gap_count, 1)
        self.assertEqual(result.missing_sequence_ranges, [[3, 3]])
        self.assertIn("sequence_gap", result.missing_reasons)

    def test_unacknowledged_tail_is_never_complete(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.checkpoint(emitted=3, acked=1, contiguous=1, pending=2)

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)

        self.assertTrue(result.tail_gap_detected)
        self.assertIn(result.overall_status, {"partial", "insufficient"})
        self.assertIn("tail_gap", result.missing_reasons)

    def test_late_event_creates_new_immutable_run(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.checkpoint(emitted=2, acked=1, contiguous=1, pending=1)
        first = generate_session_coverage(self.session, force=True)

        self.append("item_left", 2, {"leave_reason": "navigation"})
        self.checkpoint(emitted=2, acked=2, contiguous=2)
        second = generate_session_coverage(self.session, force=True)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(RecordingCoverageRun.objects.count(), 2)
        frozen_hash = first.source_hash
        first.source_hash = "0" * 64
        with self.assertRaises(ValueError):
            first.save()
        first.refresh_from_db()
        self.assertEqual(first.source_hash, frozen_hash)
        with self.assertRaises(ValueError):
            RecordingCoverageRun.objects.filter(pk=first.pk).update(
                source_hash="1" * 64
            )
        with self.assertRaises(ValueError):
            first.session_results.all().delete()

    def test_session_without_capture_contract_is_legacy_unknown(self):
        self.session.capture_contract = None
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            capture_contract=None
        )
        self.session.refresh_from_db()

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)
        attempt_result = run.attempt_results.get(attempt=self.attempt)
        self.assertEqual(result.overall_status, "legacy_unknown")
        self.assertIn("legacy_contract", result.missing_reasons)
        self.assertEqual(attempt_result.overall_status, "legacy_unknown")
        self.assertIn("legacy_contract", attempt_result.missing_reasons)

    def test_authoritative_submission_snapshot_mismatch_is_invalid(self):
        snapshot = create_response_snapshot(attempt=self.attempt, content="A")
        self.attempt.final_response_snapshot = snapshot
        self.attempt.save(update_fields=["final_response_snapshot"])
        SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=uuid.uuid4(),
            final_answer="B",
            final_response_snapshot=snapshot,
            grading_status=SubmissionEvent.GradingStatus.COMPLETED,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.append(
            "response_state_changed",
            2,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "B",
                "change_source": "student",
            },
        )
        self.checkpoint(emitted=2, acked=2, contiguous=2)

        run = generate_session_coverage(self.session, force=True)
        session_result = run.session_results.get(session=self.session)
        attempt_result = run.attempt_results.get(attempt=self.attempt)

        self.assertEqual(session_result.overall_status, "invalid")
        self.assertEqual(attempt_result.overall_status, "invalid")
        self.assertIn("final_snapshot_mismatch", attempt_result.missing_reasons)

    def test_heartbeat_alone_does_not_reset_active_time_idle_threshold(self):
        def event(event_type, monotonic_ms):
            return SimpleNamespace(
                event_type=event_type,
                server_ingest_sequence_no=monotonic_ms,
                client_instance_id=self.client_instance_id,
                client_time_origin=self.origin,
                client_monotonic_ms=Decimal(monotonic_ms),
            )

        features = _derived_features(
            [
                event("document_visible", 0),
                event("window_focused", 1),
                event("item_rendered", 2),
                event("activity_heartbeat", 20_000),
                event("activity_heartbeat", 40_000),
                event("response_state_changed", 60_002),
            ]
        )

        self.assertEqual(features["active_response_time_ms_30s"], 30_001)
        self.assertEqual(features["active_response_time_ms_60s"], 60_001)

    def test_checkpoint_for_one_instance_does_not_hide_another_unknown_tail(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.checkpoint(emitted=1, acked=1, contiguous=1)
        second_instance = uuid.uuid4()
        self.append(
            "response_state_changed",
            1,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
            client_instance_id=second_instance,
        )

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)

        self.assertIn("tail_unknown", result.missing_reasons)
        self.assertIn("checkpoint_missing", result.missing_reasons)
        self.assertNotEqual(result.overall_status, "complete")

    def test_capture_contract_required_dimensions_reduce_overall_coverage(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.append(
            "response_state_changed",
            2,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
        )
        self.append("item_left", 3, {"leave_reason": "finish"})
        self.checkpoint(emitted=3, acked=3, contiguous=3)
        self.session.status = ExperimentSession.Status.COMPLETED
        self.session.terminal_at = timezone.now()
        self.session.save(update_fields=["status", "terminal_at"])

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)

        self.assertEqual(result.overall_status, "partial")
        self.assertIn("missing_session_started", result.missing_reasons)
        self.assertIn("missing_session_terminal", result.missing_reasons)
        self.assertIn("telemetry_baseline_missing", result.missing_reasons)
        self.assertIn("time_sync_unavailable", result.missing_reasons)

    def test_zero_revisions_and_zero_returns_are_usable_facts(self):
        self.append_server(
            "item_entered",
            {"entry_reason": "session_start", "from_assignment_id": None},
        )
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.append(
            "response_state_changed",
            2,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
        )
        self.append("item_left", 3, {"leave_reason": "finish"})
        self.checkpoint(emitted=3, acked=3, contiguous=3)

        run = generate_session_coverage(self.session, force=True)
        result = run.attempt_results.get(attempt=self.attempt)

        self.assertEqual(result.analysis_eligibility["revision"]["status"], "complete")
        self.assertTrue(result.analysis_eligibility["revision"]["usable"])
        self.assertEqual(result.derived_features["revision_count"], 0)
        self.assertEqual(result.analysis_eligibility["return_behavior"]["status"], "complete")
        self.assertTrue(result.analysis_eligibility["return_behavior"]["usable"])
        self.assertEqual(result.derived_features["return_count"], 0)
        self.assertEqual(result.analysis_eligibility["text_editing"]["status"], "not_applicable")
        self.assertFalse(result.analysis_eligibility["text_editing"]["usable"])

    def test_response_chain_uses_client_sequence_instead_of_receive_order(self):
        self.append(
            "response_state_changed",
            2,
            {
                "question_type": "single_choice",
                "response_state_before": "A",
                "response_state_after": "B",
                "change_source": "student",
            },
        )
        self.append(
            "response_state_changed",
            1,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
        )

        run = generate_session_coverage(self.session, force=True)
        result = run.attempt_results.get(attempt=self.attempt)

        self.assertEqual(result.response_chain_status, "complete")
        self.assertEqual(result.derived_features["revision_count"], 1)
        self.assertNotIn("response_chain_break", result.missing_reasons)

    def test_cross_instance_response_order_without_sync_is_uncertain(self):
        self.append(
            "response_state_changed",
            1,
            {
                "question_type": "single_choice",
                "response_state_before": None,
                "response_state_after": "A",
                "change_source": "student",
            },
        )
        self.append(
            "response_state_changed",
            1,
            {
                "question_type": "single_choice",
                "response_state_before": "A",
                "response_state_after": "B",
                "change_source": "student",
            },
            client_instance_id=uuid.uuid4(),
        )

        run = generate_session_coverage(self.session, force=True)
        result = run.attempt_results.get(attempt=self.attempt)

        self.assertEqual(result.response_chain_status, "partial")
        self.assertIn("cross_instance_order_uncertain", result.missing_reasons)

    def test_time_origin_change_inside_one_instance_is_explicit(self):
        self.append("item_rendered", 1, {"presentation_state": "interactive"})
        self.append(
            "item_left",
            2,
            {"leave_reason": "navigation"},
            client_time_origin=self.origin + timedelta(seconds=1),
        )
        self.checkpoint(emitted=2, acked=2, contiguous=2)

        run = generate_session_coverage(self.session, force=True)
        result = run.session_results.get(session=self.session)

        self.assertIn("time_origin_changed", result.missing_reasons)
