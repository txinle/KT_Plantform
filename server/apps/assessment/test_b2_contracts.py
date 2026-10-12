import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.assessment.event_batch_services import append_event_batch
from apps.assessment.event_services import append_assessment_event
from apps.assessment.models import (
    AssessmentEvent,
    ExperimentSession,
    ResponseInputObservation,
)
from apps.assessment.writer_lease_services import (
    WriterLeaseConflict,
    acquire_attempt_writer_lease,
)
from apps.experiments.models import (
    Condition,
    ConditionAssignment,
    Experiment,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.question_bank.models import Question, QuestionBank
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.assessment.models import StudentQuestionAssignment, Attempt


class B2ContractsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.teacher = User.objects.create_user(username="t", password="x")
        self.student = User.objects.create_user(username="s", password="x")
        self.bank = QuestionBank.objects.create(name="b", created_by=self.teacher)
        question = Question.objects.create(bank=self.bank, sequence=1, question_type=Question.Type.SINGLE, text="q", correct_answer="A")
        self.experiment = Experiment.objects.create(name="e", question_bank=self.bank, created_by=self.teacher)
        self.version = ExperimentVersion.objects.create(experiment=self.experiment, version_number=1, published_by=self.teacher)
        snapshot = QuestionSnapshot.objects.create(version=self.version, source_question=question, source_sequence=1, code="Q1", question_type=Question.Type.SINGLE, text="q", options=[{"key":"A","text":"a"},{"key":"B","text":"b"}], correct_answer="A", primary_kc="kc", all_kcs=["kc"])
        self.session = ExperimentSession.objects.create(version=self.version, student=self.student)
        item = get_or_create_default_item_instance(snapshot)
        self.assignment = StudentQuestionAssignment.objects.create(session=self.session, snapshot=snapshot, item_instance=item, assigned_sequence_index=1)

    def test_session_event_may_omit_attempt_id(self):
        payload = {
            "client_instance_id": str(uuid.uuid4()),
            "events": [{
                "event_type": "session_started", "event_version": 1,
                "session_id": self.session.pk, "client_sequence_no": 1,
                "correlation_id": None, "client_wall_time_utc": timezone.now().isoformat(),
                "client_monotonic_ms": "1", "client_time_origin": timezone.now().isoformat(),
                "time_sync_id": None, "payload": {}, "quality_flags": [], "app_version": "test",
            }],
        }
        acks = append_event_batch(student=self.student, document=payload)
        self.assertEqual(len(acks), 1)
        self.assertIsNone(AssessmentEvent.objects.get().attempt_id)

    def test_expire_due_sessions_handles_session_without_class_task(self):
        call_command("expire_due_sessions")

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, ExperimentSession.Status.IN_PROGRESS)

    def test_expire_due_sessions_avoids_server_side_cursor_with_pgbouncer(self):
        with mock.patch(
            "django.db.models.query.QuerySet.iterator",
            side_effect=AssertionError("server-side cursor is incompatible with transaction pooling"),
        ):
            call_command("expire_due_sessions")

    def test_event_batch_reserves_one_contiguous_server_sequence(self):
        client_instance_id = str(uuid.uuid4())
        now = timezone.now().isoformat()
        payload = {
            "client_instance_id": client_instance_id,
            "events": [
                {
                    "event_type": "session_started",
                    "event_version": 1,
                    "session_id": self.session.pk,
                    "client_sequence_no": 1,
                    "correlation_id": None,
                    "client_wall_time_utc": now,
                    "client_monotonic_ms": "1",
                    "client_time_origin": now,
                    "time_sync_id": None,
                    "payload": {},
                    "quality_flags": [],
                    "app_version": "test",
                },
                {
                    "event_type": "session_completed",
                    "event_version": 1,
                    "session_id": self.session.pk,
                    "client_sequence_no": 2,
                    "correlation_id": None,
                    "client_wall_time_utc": now,
                    "client_monotonic_ms": "2",
                    "client_time_origin": now,
                    "time_sync_id": None,
                    "payload": {"completion_reason": "student_finished"},
                    "quality_flags": [],
                    "app_version": "test",
                },
            ],
        }

        acknowledgements = append_event_batch(student=self.student, document=payload)

        self.session.refresh_from_db()
        self.assertEqual(
            [row["server_ingest_sequence_no"] for row in acknowledgements],
            [1, 2],
        )
        self.assertEqual(self.session.next_ingest_sequence_no, 3)

    def test_completed_session_accepts_capture_events_queued_before_completion(self):
        attempt = Attempt.objects.create(
            session=self.session,
            assignment=self.assignment,
            status=Attempt.Status.SUBMITTED,
        )
        self.session.status = ExperimentSession.Status.COMPLETED
        self.session.completed_at = timezone.now()
        self.session.save(update_fields=["status", "completed_at"])
        now = timezone.now().isoformat()
        payload = {
            "client_instance_id": str(uuid.uuid4()),
            "events": [
                {
                    "event_type": "item_left",
                    "event_version": 2,
                    "session_id": self.session.pk,
                    "attempt_id": attempt.pk,
                    "client_sequence_no": 1,
                    "correlation_id": str(uuid.uuid4()),
                    "client_wall_time_utc": now,
                    "client_monotonic_ms": "1",
                    "client_time_origin": now,
                    "time_sync_id": None,
                    "payload": {"leave_reason": "submit", "to_assignment_id": None},
                    "quality_flags": [],
                    "app_version": "test",
                }
            ],
        }

        acknowledgements = append_event_batch(student=self.student, document=payload)

        self.assertEqual(len(acknowledgements), 1)
        self.assertTrue(
            AssessmentEvent.objects.filter(
                session=self.session,
                attempt=attempt,
                event_type="item_left",
            ).exists()
        )

    def test_cross_instance_interval_quality_is_degraded(self):
        first = append_assessment_event(
            event_type="session_started", event_version=1, event_domain="assessment", actor_source="client",
            session=self.session, payload={}, client_instance_id=uuid.uuid4(), client_sequence_no=1,
            client_wall_time_utc=timezone.now(), client_monotonic_ms=Decimal("1"), client_time_origin=timezone.now(),
        )
        second = append_assessment_event(
            event_type="session_completed", event_version=1, event_domain="assessment", actor_source="client",
            session=self.session, payload={"completion_reason":"student_finished"}, client_instance_id=uuid.uuid4(), client_sequence_no=1,
            client_wall_time_utc=timezone.now(), client_monotonic_ms=Decimal("2"), client_time_origin=timezone.now(),
        )
        self.assertEqual(first.interval_quality, "unknown")
        self.assertEqual(second.interval_quality, "degraded")

    def test_writer_lease_conflicts_and_refreshes_by_new_client(self):
        attempt = Attempt.objects.create(session=self.session, assignment=self.assignment)
        lease = acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        with self.assertRaises(WriterLeaseConflict):
            acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4())
        refreshed = acquire_attempt_writer_lease(attempt, client_instance_id=uuid.uuid4(), now=lease.expires_at + timedelta(seconds=1))
        self.assertNotEqual(lease.token, refreshed.token)

    def test_condition_assignment_carries_evidence(self):
        self.assertTrue(hasattr(Experiment, "assignment_unit_type"))
        condition = Condition.objects.create(experiment=self.experiment, key="control", label="Control")
        assignment = ConditionAssignment.objects.create(condition=condition, session=self.session, algorithm="rand", algorithm_version="v1", seed="s", draw=Decimal("0.2"), stratum="all", probability=Decimal("0.5"))
        self.assertEqual(assignment.session_id, self.session.pk)

    def test_stable_text_edit_event_creates_metadata_only_observation(self):
        before_hash = "a" * 64
        after_hash = "b" * 64
        payload = {
            "client_instance_id": str(uuid.uuid4()),
            "events": [{
                "event_type": "text_edit_observed",
                "event_version": 1,
                "session_id": self.session.pk,
                "attempt_id": Attempt.objects.create(
                    session=self.session,
                    assignment=self.assignment,
                ).pk,
                "client_sequence_no": 1,
                "correlation_id": None,
                "client_wall_time_utc": timezone.now().isoformat(),
                "client_monotonic_ms": "25.000",
                "client_time_origin": timezone.now().isoformat(),
                "time_sync_id": None,
                "payload": {
                    "question_type": "short_answer",
                    "edit_operation": "insert",
                    "content_hash_before": before_hash,
                    "content_hash_after": after_hash,
                    "content_length_before": 0,
                    "content_length_after": 2,
                    "input_method": "keyboard",
                },
                "quality_flags": [],
                "app_version": "test",
            }],
        }

        append_event_batch(student=self.student, document=payload)

        observation = ResponseInputObservation.objects.get()
        self.assertEqual(observation.content_hash_before, before_hash)
        self.assertEqual(observation.content_hash_after, after_hash)
        self.assertEqual(observation.content_length_after, 2)
        self.assertIsNone(observation.batch_start_client_monotonic_ms)
        self.assertIsNone(observation.batch_end_client_monotonic_ms)
        self.assertFalse(observation.is_ime)
        self.assertFalse(hasattr(observation, "content"))
