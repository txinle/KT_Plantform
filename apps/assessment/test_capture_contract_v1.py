import hashlib
import json
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .event_services import append_assessment_event
from .models import (
    Attempt,
    CaptureContractArtifact,
    ClientCaptureCheckpoint,
    ExperimentSession,
    StudentQuestionAssignment,
)
from .recording_capture_services import (
    ensure_capture_contract_v1,
    record_client_capture_checkpoint,
)


class CaptureContractV1Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="capture-contract-teacher", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="CAPTURE-S1",
            student_number="CAPTURE-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="采集契约题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="采集契约题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=self.teacher, question_bank=bank, name="采集契约实验"
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment, version_number=1, published_by=self.teacher
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="采集契约题目",
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

    def test_capture_contract_v1_is_canonical_and_immutable(self):
        contract = ensure_capture_contract_v1()
        canonical = json.dumps(
            contract.schema_document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertEqual(contract.contract_name, "capture-v1")
        self.assertEqual(contract.contract_version, 1)
        self.assertEqual(
            contract.schema_sha256,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        contract.schema_document = {"corrupted": True}
        with self.assertRaises(ValueError):
            contract.save()

    def test_event_student_anchor_is_server_derived(self):
        event = append_assessment_event(
            event_type="item_rendered",
            event_version=1,
            event_domain="assessment",
            actor_source="client",
            session=self.session,
            attempt=self.attempt,
            payload={"presentation_state": "interactive"},
            client_instance_id=uuid.uuid4(),
            client_sequence_no=1,
            client_wall_time_utc=timezone.now(),
            client_monotonic_ms=Decimal("10.000"),
            client_time_origin=timezone.now(),
        )
        self.assertEqual(event.student_id, self.student.pk)

    def test_session_student_and_contract_are_immutable(self):
        other = User.objects.create_user(
            username="CAPTURE-S2",
            student_number="CAPTURE-S2",
            role=User.Role.STUDENT,
        )
        self.session.student = other
        with self.assertRaises(ValidationError):
            self.session.save()

        self.session.refresh_from_db()
        self.session.capture_contract = None
        with self.assertRaises(ValidationError):
            self.session.save()

    def test_checkpoint_preserves_ack_and_tail_evidence_idempotently(self):
        client_instance_id = uuid.uuid4()
        checkpoint_id = uuid.uuid4()
        document = {
            "checkpoint_id": str(checkpoint_id),
            "session_id": self.session.pk,
            "client_instance_id": str(client_instance_id),
            "highest_emitted_sequence_no": 8,
            "highest_acked_sequence_no": 7,
            "highest_contiguous_acked_sequence_no": 6,
            "pending_event_count": 2,
            "failed_event_count": 0,
            "missing_local_sequence_ranges": [[7, 8]],
            "client_monotonic_ms": "1200.500",
            "client_wall_time_utc": timezone.now().isoformat(),
            "client_time_origin": timezone.now().isoformat(),
            "time_sync_id": None,
            "trigger": "navigation",
            "quality_flags": ["clock_unsynchronized"],
            "app_version": "test",
        }
        first = record_client_capture_checkpoint(
            student=self.student, document=document
        )
        replay = record_client_capture_checkpoint(
            student=self.student, document=document
        )

        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(ClientCaptureCheckpoint.objects.count(), 1)
        self.assertEqual(first.highest_contiguous_acked_sequence_no, 6)
        self.assertEqual(first.missing_local_sequence_ranges, [[7, 8]])

        changed = {**document, "pending_event_count": 1}
        with self.assertRaises(ValidationError):
            record_client_capture_checkpoint(student=self.student, document=changed)
