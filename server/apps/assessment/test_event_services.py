import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection, transaction, IntegrityError
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .event_schema_registry import EventPayloadValidationError
from .event_services import (
    EventIdempotencyConflict,
    EventSchemaDriftError,
    append_assessment_event,
)
from .models import (
    AssessmentEvent,
    AppendOnlyEventError,
    Attempt,
    ClientTimeSync,
    ConfidenceAssignment,
    EventSchemaArtifact,
    ExperimentSession,
    StudentQuestionAssignment,
    SubmissionEvent,
    _event_schema_artifact_append_scope,
)


class AssessmentEventAppendServiceTests(TestCase):
    def test_server_clock_reversal_preserves_raw_times_and_sequence(self):
        received = timezone.now()
        sampled = received - timedelta(seconds=1)
        with patch('apps.assessment.event_services.timezone') as clock:
            clock.now.side_effect = [received, sampled]
            event = self.append()
        self.assertEqual(event.server_received_at, received)
        self.assertEqual(event.server_committed_at, sampled)
        self.assertTrue(event.server_clock_reversed)
        self.assertGreaterEqual(event.server_processing_elapsed_ms, 0)
        self.assertEqual(event.server_ingest_sequence_no, 1)
        replay = self.append()
        self.assertEqual(replay.pk, event.pk)
        self.assertEqual(replay.server_committed_at, sampled)

    def test_forward_clock_jump_keeps_actual_sample(self):
        received = timezone.now()
        sampled = received + timedelta(minutes=1)
        with patch('apps.assessment.event_services.timezone') as clock:
            clock.now.side_effect = [received, sampled]
            event = self.append()
        self.assertEqual(event.server_committed_at, sampled)
        self.assertFalse(event.server_clock_reversed)
        self.assertGreaterEqual(event.server_processing_elapsed_ms, 0)

    def test_unmarked_clock_reversal_still_violates_database_constraint(self):
        event = self.append()
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE assessment_assessmentevent SET server_committed_at = %s WHERE event_id = %s',
                    [event.server_received_at - timedelta(seconds=1), event.pk],
                )

    def test_clock_reversed_event_rolls_back_with_transaction(self):
        received = timezone.now()
        with self.assertRaisesRegex(RuntimeError, 'abort clock event'):
            with transaction.atomic(), patch('apps.assessment.event_services.timezone') as clock:
                clock.now.side_effect = [received, received - timedelta(seconds=1)]
                self.append()
                raise RuntimeError('abort clock event')
        self.assertFalse(AssessmentEvent.objects.exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, 1)

    def test_server_appends_reuse_the_held_session_lock_and_allocate_distinct_sequences(self):
        from .event_services import _register_schema_artifact
        _register_schema_artifact('item_entered', 1)
        with transaction.atomic():
            self.session = ExperimentSession.objects.select_for_update().get(pk=self.session.pk)
            with CaptureQueriesContext(connection) as queries:
                first = self.append(client_instance_id=None, client_sequence_no=None, session_lock_held=True)
                second = self.append(client_instance_id=None, client_sequence_no=None, session_lock_held=True)
            third = self.append(client_instance_id=None, client_sequence_no=None)
            fourth = self.append(client_instance_id=None, client_sequence_no=None, session_lock_held=True)
        self.assertEqual((first.server_ingest_sequence_no, second.server_ingest_sequence_no), (1, 2))
        self.assertEqual((third.server_ingest_sequence_no, fourth.server_ingest_sequence_no), (3, 4))
        self.assertFalse(any('FOR UPDATE' in row['sql'] for row in queries))
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, 5)

    def test_held_session_event_sequence_and_event_roll_back_together(self):
        with self.assertRaisesRegex(RuntimeError, 'abort submission'):
            with transaction.atomic():
                self.session = ExperimentSession.objects.select_for_update().get(pk=self.session.pk)
                self.append(client_instance_id=None, client_sequence_no=None, session_lock_held=True)
                raise RuntimeError('abort submission')
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, 1)
        self.assertFalse(AssessmentEvent.objects.exists())

    def test_append_reuses_validated_links_instead_of_requerying_each_foreign_key(self):
        from .event_services import _register_schema_artifact
        _register_schema_artifact('item_entered', 1)
        with CaptureQueriesContext(connection) as queries:
            event = self.append()
        related_tables = ('accounts_user', 'assessment_experimentsession', 'assessment_attempt',
                          'assessment_studentquestionassignment', 'experiments_questionsnapshot', 'experiments_iteminstance')
        duplicate_link_checks = [row['sql'] for row in queries if row['sql'].startswith('SELECT 1 AS "a"')
                                 and any(f'FROM "{table}"' in row['sql'] for table in related_tables)]
        self.assertEqual(duplicate_link_checks, [])
        self.assertEqual(event.student_id, self.student.pk)
        self.assertEqual(event.assignment_id, self.assignment.pk)
        self.assertEqual(event.question_snapshot_id, self.snapshot.pk)

    def test_database_referential_constraints_still_reject_an_orphan_and_rollback_sequence(self):
        self.session.student_id = 999999999
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.append()
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.assertFalse(AssessmentEvent.objects.exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, 1)

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

    def test_attempt_linkage_still_validates_snapshot_when_assignment_is_omitted(self):
        other_snapshot = QuestionSnapshot.objects.create(
            version=self.snapshot.version,
            source_question=self.snapshot.source_question,
            source_sequence=2,
            code="Q2",
            question_type=Question.Type.SINGLE,
            text="不属于当前 attempt 的快照",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="B",
            primary_kc="其他 KC",
            all_kcs=["其他 KC"],
        )

        with self.assertRaises(ValueError):
            self.append(assignment=None, question_snapshot=other_snapshot)

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
            client_received_wall_time_utc=now + timedelta(milliseconds=30),
            client_received_monotonic_ms=Decimal("40.000"),
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
            finalized_at=now + timedelta(milliseconds=40),
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

    def test_cross_instance_sync_is_rejected_instead_of_linked(self):
        now = timezone.now()
        sync = ClientTimeSync.objects.create(
            client_instance_id=uuid.uuid4(),
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=now,
            server_received_at=now,
            server_responded_at=now,
            client_received_wall_time_utc=now + timedelta(milliseconds=30),
            client_received_monotonic_ms=Decimal("40.000"),
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
            finalized_at=now + timedelta(milliseconds=40),
        )

        with self.assertRaises(ValueError):
            self.append(client_wall_time_utc=now, time_sync=sync)

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_cross_session_sync_is_rejected_even_for_the_same_client_instance(self):
        now = timezone.now()
        other_student = User.objects.create_user(
            username="EVENT-SERVICE-S3",
            student_number="EVENT-SERVICE-S3",
            role=User.Role.STUDENT,
        )
        other_session = ExperimentSession.objects.create(
            version=self.session.version,
            student=other_student,
        )
        sync = ClientTimeSync.objects.create(
            session=other_session,
            client_instance_id=self.client_instance_id,
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=now,
            server_received_at=now,
            server_responded_at=now,
            client_received_wall_time_utc=now + timedelta(milliseconds=30),
            client_received_monotonic_ms=Decimal("40.000"),
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
            finalized_at=now + timedelta(milliseconds=40),
        )

        with self.assertRaises(ValueError):
            self.append(
                client_wall_time_utc=now,
                client_monotonic_ms=Decimal("50.000"),
                client_time_origin=now,
                time_sync=sync,
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_stale_sync_is_not_linked_or_used_for_precision(self):
        now = timezone.now()
        origin = now - timedelta(hours=1)
        sync = ClientTimeSync.objects.create(
            client_instance_id=self.client_instance_id,
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=origin,
            server_received_at=now,
            server_responded_at=now,
            client_received_wall_time_utc=now + timedelta(milliseconds=30),
            client_received_monotonic_ms=Decimal("40.000"),
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality=ClientTimeSync.SyncQuality.VALID,
            finalized_at=now + timedelta(milliseconds=40),
        )

        event = self.append(
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("1200011.000"),
            client_time_origin=origin,
            time_sync=sync,
        )

        self.assertIsNone(event.time_sync_id)
        self.assertIsNone(event.estimated_occurred_at_utc)
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

    def test_append_freezes_the_exact_event_schema_artifact(self):
        event = self.append()

        artifact = EventSchemaArtifact.objects.get(
            event_type=event.event_type,
            event_version=event.event_version,
        )
        self.assertRegex(artifact.schema_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(artifact.schema_document["additionalProperties"], False)

    def test_append_rejects_same_version_schema_drift(self):
        with _event_schema_artifact_append_scope():
            EventSchemaArtifact.objects.create(
                event_type="item_entered",
                event_version=1,
                schema_sha256="0" * 64,
                schema_document={"corrupted": True},
            )

        with self.assertRaises(EventSchemaDriftError):
            self.append()

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_default_manager_cannot_bypass_the_sole_append_service(self):
        direct_values = {
            "event_domain": "assessment",
            "event_type": "item_rendered",
            "event_version": 1,
            "envelope_version": 1,
            "actor_source": "client",
            "session": self.session,
            "assignment": self.assignment,
            "attempt": self.attempt,
            "question_snapshot": self.snapshot,
            "item_instance": self.item_instance,
            "client_instance_id": uuid.uuid4(),
            "client_sequence_no": 1,
            "server_ingest_sequence_no": 1,
            "payload": {},
        }

        with self.assertRaises(AppendOnlyEventError):
            AssessmentEvent.objects.create(**direct_values)
        with self.assertRaises(AppendOnlyEventError):
            AssessmentEvent.objects.bulk_create([AssessmentEvent(**direct_values)])

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_response_committed_requires_an_authoritative_submission(self):
        payload = {
            "submission_token": str(uuid.uuid4()),
            "score": 1,
            "is_correct": True,
        }

        with self.assertRaises(ValueError):
            self.append(
                event_type="response_committed",
                actor_source="server",
                payload=payload,
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_client_source_cannot_claim_an_authoritative_commit(self):
        token = uuid.uuid4()
        SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=token,
            final_answer="A",
            is_correct=True,
            score=1,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )

        with self.assertRaises(ValueError):
            self.append(
                event_type="response_committed",
                actor_source="client",
                payload={
                    "submission_token": str(token),
                    "score": 1,
                    "is_correct": True,
                },
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_client_source_cannot_claim_a_submitted_attempt_close(self):
        with self.assertRaises(ValueError):
            self.append(
                event_type="attempt_closed",
                actor_source="client",
                payload={"close_reason": "submitted"},
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_item_event_cannot_link_only_a_foreign_snapshot_to_the_session(self):
        foreign_experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.snapshot.source_question.bank,
            name="外部实验",
        )
        foreign_version = ExperimentVersion.objects.create(
            experiment=foreign_experiment,
            version_number=1,
            published_by=self.teacher,
        )
        foreign_snapshot = QuestionSnapshot.objects.create(
            version=foreign_version,
            source_question=self.snapshot.source_question,
            source_sequence=1,
            code="FOREIGN-Q1",
            question_type=Question.Type.SINGLE,
            text="外部快照",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
            primary_kc="外部 KC",
            all_kcs=["外部 KC"],
        )

        with self.assertRaises(ValueError):
            self.append(
                event_type="item_rendered",
                payload={},
                assignment=None,
                attempt=None,
                question_snapshot=foreign_snapshot,
                item_instance=None,
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_confidence_event_id_must_match_the_attempt_projection(self):
        ConfidenceAssignment.objects.create(
            attempt=self.attempt,
            condition="prompt",
            assignment_unit="attempt",
            assignment_version="v1",
            response_status="assigned",
        )

        with self.assertRaises(ValueError):
            self.append(
                event_type="confidence_committed",
                payload={
                    "confidence_assignment_id": str(uuid.uuid4()),
                    "probability": 80,
                },
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_feedback_and_submitted_close_require_committed_business_fact(self):
        with self.assertRaises(ValueError):
            self.append(
                event_type="feedback_shown",
                payload={
                    "feedback_mode": "full",
                    "included_correctness": True,
                    "included_correct_answer": True,
                    "included_explanation": True,
                },
            )
        with self.assertRaises(ValueError):
            self.append(
                event_type="attempt_closed",
                payload={"close_reason": "submitted"},
            )

        self.assertEqual(AssessmentEvent.objects.count(), 0)
