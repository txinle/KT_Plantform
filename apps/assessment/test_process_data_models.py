import hashlib
import uuid
from importlib import import_module
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .models import (
    AppendOnlyEventError,
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
    _assessment_event_append_scope,
)


def process_model(name):
    try:
        return django_apps.get_model("assessment", name)
    except LookupError:
        return None


class ProcessDataMigrationTests(SimpleTestCase):
    def test_brin_index_is_postgresql_only(self):
        migration = import_module(
            "apps.assessment.migrations.0004_process_data_contracts"
        )
        executed = []
        sqlite_editor = SimpleNamespace(
            connection=SimpleNamespace(vendor="sqlite"),
            execute=executed.append,
        )
        postgres_editor = SimpleNamespace(
            connection=SimpleNamespace(vendor="postgresql"),
            execute=executed.append,
        )

        migration.create_event_received_brin(None, sqlite_editor)
        self.assertEqual(executed, [])

        migration.create_event_received_brin(None, postgres_editor)
        self.assertEqual(len(executed), 1)
        self.assertIn("USING BRIN (server_received_at)", executed[0])


class ProcessDataModelTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="process-model-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="PROCESS-MODEL-S1",
            student_number="PROCESS-MODEL-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="过程模型题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="过程模型题目",
            correct_answer="答案",
        )
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="过程模型实验",
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
            question_type=Question.Type.SHORT,
            text="过程模型题目",
            correct_answer="答案",
            primary_kc="过程 KC",
            all_kcs=["过程 KC"],
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

    def assert_process_models_exist(self):
        for name in (
            "ClientTimeSync",
            "ResponseSnapshot",
            "SimulationSnapshot",
            "ConfidenceAssignment",
            "AssessmentEvent",
        ):
            self.assertIsNotNone(process_model(name), name)

    def test_all_process_data_models_exist(self):
        self.assert_process_models_exist()

    def test_time_sync_preserves_client_instance_scope_and_estimates(self):
        ClientTimeSync = process_model("ClientTimeSync")
        self.assertIsNotNone(ClientTimeSync)
        client_instance_id = uuid.uuid4()
        now = timezone.now()

        sync = ClientTimeSync.objects.create(
            client_instance_id=client_instance_id,
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("1500.250"),
            client_time_origin=now,
            server_received_at=now,
            server_responded_at=now,
            estimated_clock_offset_ms=125,
            estimated_rtt_ms=20,
            sync_quality="valid",
        )

        self.assertEqual(sync.client_instance_id, client_instance_id)
        self.assertEqual(sync.estimated_clock_offset_ms, 125)
        self.assertEqual(sync.estimated_rtt_ms, 20)

    def test_response_snapshot_hash_is_unique_only_within_an_attempt(self):
        ResponseSnapshot = process_model("ResponseSnapshot")
        self.assertIsNotNone(ResponseSnapshot)
        content_hash = hashlib.sha256("第一版回答".encode("utf-8")).hexdigest()
        ResponseSnapshot.objects.create(
            attempt=self.attempt,
            content="第一版回答",
            content_hash=content_hash,
            content_length=5,
            schema_version=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ResponseSnapshot.objects.create(
                attempt=self.attempt,
                content="第一版回答",
                content_hash=content_hash,
                content_length=5,
                schema_version=1,
            )

    def test_response_snapshot_rejects_false_content_claims_and_updates(self):
        ResponseSnapshot = process_model("ResponseSnapshot")
        self.assertIsNotNone(ResponseSnapshot)

        with self.assertRaises(ValidationError):
            ResponseSnapshot.objects.create(
                attempt=self.attempt,
                content="真实内容",
                content_hash="0" * 64,
                content_length=999,
                schema_version=1,
            )

        content = "可验证内容"
        snapshot = ResponseSnapshot.objects.create(
            attempt=self.attempt,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content_length=len(content),
            schema_version=1,
        )
        snapshot.content = "被篡改"
        with self.assertRaises(AppendOnlyEventError):
            snapshot.save()

    def test_simulation_snapshot_keeps_large_state_outside_event_payload(self):
        SimulationSnapshot = process_model("SimulationSnapshot")
        self.assertIsNotNone(SimulationSnapshot)
        from .snapshot_services import create_simulation_snapshot

        content = b"x" * 2048
        snapshot = create_simulation_snapshot(
            attempt=self.attempt,
            simulation_version="circuit-v1",
            state_metadata={"component_count": 8},
            blob_storage_key="simulation/2026/08/state.bin",
            content_bytes=content,
            schema_version=1,
        )

        self.assertEqual(snapshot.state_metadata, {"component_count": 8})
        self.assertEqual(snapshot.blob_storage_key, "simulation/2026/08/state.bin")
        self.assertEqual(snapshot.content_size, 2048)
        self.assertEqual(
            snapshot.content_hash,
            hashlib.sha256(content).hexdigest(),
        )

        with self.assertRaises(AppendOnlyEventError):
            SimulationSnapshot.objects.create(
                attempt=self.attempt,
                simulation_version="circuit-v1",
                state_metadata={"component_count": 8},
                blob_storage_key="simulation/2026/08/false.bin",
                content_hash="b" * 64,
                content_size=2048,
                schema_version=1,
            )

    def test_confidence_assignment_distinguishes_assignment_and_response_states(self):
        ConfidenceAssignment = process_model("ConfidenceAssignment")
        self.assertIsNotNone(ConfidenceAssignment)
        now = timezone.now()

        assignment = ConfidenceAssignment.objects.create(
            attempt=self.attempt,
            condition="prompt",
            assigned_at=now,
            assignment_unit="attempt",
            assignment_version="v1",
            prompt_shown_at=now,
            response_status="committed",
            probability=80,
            committed_at=now,
        )

        self.assertEqual(assignment.probability, 80)
        self.assertEqual(assignment.response_status, "committed")

    def test_committed_confidence_requires_value_and_commit_time(self):
        ConfidenceAssignment = process_model("ConfidenceAssignment")
        self.assertIsNotNone(ConfidenceAssignment)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfidenceAssignment.objects.create(
                attempt=self.attempt,
                condition="prompt",
                assignment_unit="attempt",
                assignment_version="v1",
                response_status="committed",
                probability=None,
                committed_at=None,
            )

    def create_event(self, **overrides):
        AssessmentEvent = process_model("AssessmentEvent")
        self.assertIsNotNone(AssessmentEvent)
        values = {
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
            "correlation_id": uuid.uuid4(),
            "capture_mode": "native",
            "quality_flags": ["clock_unsynchronized", "offline_buffered"],
            "payload": {},
            "app_version": "test",
        }
        values.update(overrides)
        with _assessment_event_append_scope():
            return AssessmentEvent.objects.create(**values)

    def test_event_envelope_keeps_ingest_order_distinct_from_client_order(self):
        event = self.create_event(
            client_sequence_no=7,
            server_ingest_sequence_no=3,
        )

        self.assertEqual(event.client_sequence_no, 7)
        self.assertEqual(event.server_ingest_sequence_no, 3)
        self.assertIsNone(event.estimated_occurred_at_utc)
        self.assertEqual(
            event.quality_flags,
            ["clock_unsynchronized", "offline_buffered"],
        )

    def test_client_sequence_is_unique_inside_the_client_instance(self):
        client_instance_id = uuid.uuid4()
        self.create_event(
            client_instance_id=client_instance_id,
            client_sequence_no=4,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(
                client_instance_id=client_instance_id,
                client_sequence_no=4,
                server_ingest_sequence_no=2,
            )

    def test_server_ingest_sequence_is_unique_inside_the_session(self):
        self.create_event(server_ingest_sequence_no=8)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(
                client_sequence_no=2,
                server_ingest_sequence_no=8,
            )

    def test_client_sequence_starts_at_one_when_present(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(client_sequence_no=0)

    def test_database_allows_only_one_committed_event_per_attempt(self):
        self.create_event(
            event_type="response_committed",
            payload={
                "submission_token": str(uuid.uuid4()),
                "score": 1,
                "is_correct": True,
            },
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(
                event_type="response_committed",
                client_sequence_no=2,
                server_ingest_sequence_no=2,
                payload={
                    "submission_token": str(uuid.uuid4()),
                    "score": 1,
                    "is_correct": True,
                },
            )

    def test_quality_flags_reject_unknown_measurement_claims(self):
        event = self.create_event(quality_flags=["invented_precision"])

        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_events_are_append_only_through_model_and_default_queryset(self):
        event = self.create_event()
        event.payload = {"changed": True}

        with self.assertRaises(RuntimeError):
            event.save(update_fields=["payload"])
        with self.assertRaises(RuntimeError):
            type(event).objects.filter(pk=event.pk).update(payload={"changed": True})
        with self.assertRaises(RuntimeError):
            type(event).objects.filter(pk=event.pk).delete()
        with self.assertRaises(RuntimeError):
            event.delete()

        event.refresh_from_db()
        self.assertEqual(event.payload, {})
