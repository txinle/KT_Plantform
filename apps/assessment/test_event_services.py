import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .event_schema_registry import EventPayloadValidationError
from .event_services import EventIdempotencyConflict, append_assessment_event
from .models import (
    AssessmentEvent,
    Attempt,
    ClientTimeSync,
    ExperimentSession,
    StudentQuestionAssignment,
)


class AssessmentEventAppendServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="event-service-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="EVENT-SERVICE-S1",
            student_number="EVENT-SERVICE-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="事件服务题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="事件服务题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="事件服务实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="事件服务题目",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
            primary_kc="事件 KC",
            all_kcs=["事件 KC"],
        )
        self.item_instance = get_or_create_default_item_instance(self.snapshot)
        self.session = ExperimentSession.objects.create(
            version=version,
            student=self.student,
        )
        self.assignment = StudentQuestionAssignment.objects.create(
            session=self.session,
            snapshot=self.snapshot,
            item_instance=self.item_instance,
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(
            session=self.session,
            assignment=self.assignment,
        )
        self.client_instance_id = uuid.uuid4()

    def append(self, **overrides):
        values = {
            "event_type": "item_entered",
            "event_version": 1,
            "event_domain": "assessment",
            "actor_source": "client",
            "session": self.session,
            "assignment": self.assignment,
            "attempt": self.attempt,
            "question_snapshot": self.snapshot,
            "item_instance": self.item_instance,
            "payload": {
                "entry_reason": "navigation_click",
                "from_assignment_id": None,
            },
            "client_instance_id": self.client_instance_id,
            "client_sequence_no": 1,
            "correlation_id": uuid.UUID("5f866af5-7d35-4d7b-b997-42ba15eb62ea"),
            "capture_mode": "native",
            "quality_flags": (),
            "app_version": "test-v1",
        }
        values.update(overrides)
        return append_assessment_event(**values)

    def test_payload_is_validated_before_any_event_is_appended(self):
        with self.assertRaises(EventPayloadValidationError):
            self.append(payload={"entry_reason": "navigation_click"})

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_schema_valid_empty_payload_is_not_treated_as_missing(self):
        event = self.append(event_type="item_rendered", payload={})

        self.assertEqual(event.payload, {})

    def test_cross_session_or_assignment_links_are_rejected(self):
        other_session = ExperimentSession.objects.create(
            version=self.session.version,
            student=User.objects.create_user(
                username="EVENT-SERVICE-S2",
                student_number="EVENT-SERVICE-S2",
                role=User.Role.STUDENT,
            ),
        )

        with self.assertRaises(ValueError):
            self.append(session=other_session)

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_ingest_sequence_is_allocated_per_session(self):
        first = self.append(client_sequence_no=9)
        second = self.append(client_sequence_no=3)

        self.assertEqual(first.server_ingest_sequence_no, 1)
        self.assertEqual(second.server_ingest_sequence_no, 2)
        self.assertEqual(first.client_sequence_no, 9)
        self.assertEqual(second.client_sequence_no, 3)

    def test_identical_client_replay_returns_the_persisted_event(self):
        first = self.append()
        replay = self.append()

        self.assertEqual(replay.event_id, first.event_id)
        self.assertEqual(AssessmentEvent.objects.count(), 1)
        self.assertEqual(first.server_ingest_sequence_no, 1)

    def test_conflicting_client_replay_is_explicit_and_non_mutating(self):
        first = self.append()

        with self.assertRaises(EventIdempotencyConflict) as raised:
            self.append(
                payload={
                    "entry_reason": "resume",
                    "from_assignment_id": None,
                }
            )

        first.refresh_from_db()
        self.assertEqual(raised.exception.existing_event_id, first.event_id)
        self.assertEqual(first.payload["entry_reason"], "navigation_click")
        self.assertEqual(AssessmentEvent.objects.count(), 1)

    def test_valid_matching_time_sync_produces_an_explicit_estimate(self):
        now = timezone.now()
        sync = ClientTimeSync.objects.create(
            client_instance_id=self.client_instance_id,
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=now - timedelta(seconds=10),
            server_received_at=now,
            server_responded_at=now + timedelta(milliseconds=20),
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
        )

        event = self.append(
            client_wall_time_utc=now - timedelta(seconds=1),
            client_monotonic_ms=Decimal("9000.000"),
            client_time_origin=now - timedelta(seconds=10),
            time_sync=sync,
        )

        self.assertEqual(event.estimated_clock_offset_ms, 125)
        self.assertEqual(event.estimated_rtt_ms, 20)
        self.assertEqual(
            event.estimated_occurred_at_utc,
            now - timedelta(seconds=1) + timedelta(milliseconds=125),
        )
        self.assertNotIn("clock_unsynchronized", event.quality_flags)

    def test_invalid_or_cross_instance_sync_does_not_manufacture_precision(self):
        now = timezone.now()
        sync = ClientTimeSync.objects.create(
            client_instance_id=uuid.uuid4(),
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=now,
            server_received_at=now,
            server_responded_at=now,
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
        )

        event = self.append(client_wall_time_utc=now, time_sync=sync)

        self.assertIsNone(event.estimated_occurred_at_utc)
        self.assertIsNone(event.estimated_clock_offset_ms)
        self.assertIsNone(event.estimated_rtt_ms)
        self.assertIn("clock_unsynchronized", event.quality_flags)

    def test_quality_flags_are_deduplicated_and_correlation_is_preserved(self):
        correlation_id = uuid.uuid4()
        event = self.append(
            correlation_id=correlation_id,
            quality_flags=("offline_buffered", "offline_buffered", "late_arrival"),
        )

        self.assertEqual(event.correlation_id, correlation_id)
        self.assertEqual(event.quality_flags, ["offline_buffered", "late_arrival"])

    def test_client_sequence_fields_must_be_supplied_together(self):
        with self.assertRaises(ValueError):
            self.append(client_sequence_no=None)

        self.assertEqual(AssessmentEvent.objects.count(), 0)
