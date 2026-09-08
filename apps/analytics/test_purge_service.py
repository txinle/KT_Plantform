import os
import tempfile
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Course, DataPurgeRequest, User
from apps.analytics.models import (
    ExportJob,
    ExportSnapshot,
    RecordingCoverageAttempt,
    RecordingCoverageSession,
)
from apps.analytics.recording_coverage import generate_scope_coverage
from apps.analytics.services import execute_purge_request
from apps.assessment.capture_v2_services import ingest_capture_batch
from apps.assessment.models import (
    AssessmentEvent,
    Attempt,
    ClientCaptureCheckpoint,
    ClientTimeSync,
    ConfidenceAssignment,
    DraftSaveEvent,
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    ResponseInputObservation,
    ResponseSnapshot,
    SimulationSnapshot,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.assessment.recording_capture_services import ensure_capture_contract_v2
from apps.assessment.response_input_services import record_stable_input_observation
from apps.assessment.response_snapshot_services import ResponseState
from apps.assessment.snapshot_services import create_simulation_snapshot
from apps.experiments.models import (
    Condition,
    ConditionAssignment,
    Experiment,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank


class PurgeServiceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.teacher = User.objects.create_user(
            username="purge-teacher", role=User.Role.TEACHER
        )
        self.target_student = User.objects.create_user(
            username="purge-target", role=User.Role.STUDENT,
            student_number="PURGE-1",
        )
        self.other_student = User.objects.create_user(
            username="purge-other", role=User.Role.STUDENT,
            student_number="PURGE-2",
        )
        self.target = self._create_runtime("target", self.target_student)
        self.other = self._create_runtime("other", self.other_student)
        self.coverage_run = generate_scope_coverage(
            [self.target["session"], self.other["session"]],
            scope_type="cohort",
            scope_definition={
                "session_ids": [
                    self.target["session"].pk,
                    self.other["session"].pk,
                ]
            },
            force=True,
        )
        self.target_export = self._create_export(self.target, "target.zip")
        self.other_export = self._create_export(self.other, "other.zip")
        self.request = DataPurgeRequest.objects.create(
            scope_type="course",
            scope_definition={"course_id": self.target["course"].pk},
            reason_code="collection_error",
            requested_by=self.teacher,
            preview_counts={"sessions": 1, "events": 1},
        )

    def _create_runtime(self, suffix, student):
        course = Course.objects.create(
            owner=self.teacher,
            name=f"Purge {suffix}",
            code=f"PURGE-{suffix}",
            term="2026秋",
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=course,
            name=f"Bank {suffix}",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text=f"Question {suffix}",
            correct_answer="answer",
        )
        experiment = Experiment.objects.create(
            course=course,
            created_by=self.teacher,
            question_bank=bank,
            name=f"Experiment {suffix}",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        question_snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code=f"Q-{suffix}",
            question_type=Question.Type.SHORT,
            text=question.text,
            correct_answer=question.correct_answer,
            primary_kc="KC",
            all_kcs=["KC"],
        )
        session = ExperimentSession.objects.create(
            version=version,
            student=student,
            capture_contract=ensure_capture_contract_v2(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=question_snapshot,
            item_instance=get_or_create_default_item_instance(question_snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(session=session, assignment=assignment)
        session.current_assignment = assignment
        session.save(update_fields=["current_assignment"])

        client_instance_id = uuid.uuid4()
        before = ResponseState(Question.Type.SHORT, "")
        after = ResponseState(Question.Type.SHORT, "answer")
        event_id = uuid.uuid4()
        ingest_capture_batch(
            student=student,
            document={
                "client_instance_id": str(client_instance_id),
                "events": [{
                    "event_id": str(event_id),
                    "event_type": "response_transition",
                    "event_version": 1,
                    "session_id": session.pk,
                    "attempt_id": attempt.pk,
                    "client_sequence_no": 1,
                    "client_wall_time_utc": timezone.now().isoformat(),
                    "client_monotonic_ms": 100,
                    "client_time_origin": "2026-09-08T00:00:00Z",
                    "quality_flags": [],
                    "app_version": "capture-v2-test",
                    "payload": {
                        "capture_version": "capture-v2",
                        "operation_id": str(uuid.uuid4()),
                        "branch_id": str(client_instance_id),
                        "parent_event_id": None,
                        "before_state": before.state_json,
                        "after_state": after.state_json,
                        "before_state_hash": before.state_hash,
                        "after_state_hash": after.state_hash,
                        "input_type": "insertText",
                        "source": "student",
                    },
                }],
            },
        )
        event = AssessmentEvent.objects.get(pk=event_id)
        attempt.active_response_event = event
        attempt.final_response_event = event
        attempt.current_response_snapshot_id = event.payload["after_snapshot_id"]
        attempt.final_response_snapshot_id = event.payload["after_snapshot_id"]
        attempt.response_version = 1
        attempt.save(update_fields=[
            "active_response_event",
            "final_response_event",
            "current_response_snapshot",
            "final_response_snapshot",
            "response_version",
        ])
        response_snapshot = ResponseSnapshot.objects.get(
            pk=event.payload["after_snapshot_id"]
        )
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="answer",
            final_response_snapshot=response_snapshot,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(submission=submission)
        ConfidenceAssignment.objects.create(
            attempt=attempt,
            condition=ConfidenceAssignment.Condition.CONTROL,
            assignment_unit=ConfidenceAssignment.AssignmentUnit.ATTEMPT,
            assignment_version="test",
            response_status=ConfidenceAssignment.ResponseStatus.ASSIGNED,
        )
        DraftSaveEvent.objects.create(attempt=attempt, answer_hash="0" * 64)
        QuestionVisitEvent.objects.create(
            session=session,
            attempt=attempt,
            from_assignment=assignment,
            visit_index=1,
            event_sequence=1,
        )
        record_stable_input_observation(
            attempt=attempt,
            source_event=event,
            client_instance_id=client_instance_id,
            client_sequence_no=1,
            client_monotonic_ms=Decimal("100"),
            edit_operation=ResponseInputObservation.EditOperation.INSERT,
            content_hash_before="0" * 64,
            content_hash_after="1" * 64,
            content_length_before=0,
            content_length_after=6,
        )
        create_simulation_snapshot(
            attempt=attempt,
            simulation_version="test",
            state_metadata={"state": "answer"},
            blob_storage_key=f"purge/{suffix}",
            content_bytes=b"snapshot",
        )
        now = timezone.now()
        time_sync = ClientTimeSync.objects.create(
            session=session,
            client_instance_id=client_instance_id,
            client_wall_time_utc=now,
            client_monotonic_ms=Decimal("100"),
            client_time_origin=now,
            server_received_at=now,
            server_responded_at=now,
        )
        ClientCaptureCheckpoint.objects.create(
            session=session,
            student=student,
            client_instance_id=client_instance_id,
            highest_emitted_sequence_no=1,
            highest_acked_sequence_no=1,
            highest_contiguous_acked_sequence_no=1,
            pending_event_count=0,
            failed_event_count=0,
            missing_local_sequence_ranges=[],
            client_monotonic_ms=Decimal("100"),
            client_wall_time_utc=now,
            client_time_origin=now,
            time_sync=time_sync,
            trigger=ClientCaptureCheckpoint.Trigger.COMMIT,
            quality_flags=[],
            app_version="capture-v2-test",
        )
        condition = Condition.objects.create(
            experiment=experiment, key=f"condition-{suffix}"
        )
        ConditionAssignment.objects.create(
            condition=condition,
            session=session,
            algorithm="test",
            algorithm_version="1",
            seed="seed",
            draw=Decimal("0.5"),
            probability=Decimal("1"),
        )
        return {
            "course": course,
            "bank": bank,
            "question": question,
            "experiment": experiment,
            "version": version,
            "question_snapshot": question_snapshot,
            "session": session,
            "assignment": assignment,
            "attempt": attempt,
        }

    def _create_export(self, runtime, filename):
        path = os.path.join(self.temp_dir.name, filename)
        with open(path, "wb") as artifact:
            artifact.write(b"export")
        snapshot = ExportSnapshot.objects.create(
            course=runtime["course"],
            experiment=runtime["experiment"],
            created_by=self.teacher,
            source_bundle={"tables": {"attempts": [{"answer": "private"}]}},
            source_bundle_hash="b" * 64,
        )
        job = ExportJob.objects.create(
            course=runtime["course"],
            experiment=runtime["experiment"],
            snapshot=snapshot,
            created_by=self.teacher,
            status=ExportJob.Status.SUCCEEDED,
            artifact_path=path,
            artifact_sha256="a" * 64,
        )
        return {"snapshot": snapshot, "job": job, "path": path}

    def test_purge_removes_complete_runtime_scope_and_preserves_other_course(self):
        old_session_id = self.target["session"].pk
        result = execute_purge_request(self.request, executed_by=self.teacher)

        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["client_capture_checkpoints"], 1)
        self.assertEqual(result["recording_coverage_sessions"], 1)
        self.assertEqual(result["recording_coverage_attempts"], 1)
        self.assertFalse(
            ExperimentSession.objects.filter(pk=old_session_id).exists()
        )
        for model in (
            StudentQuestionAssignment,
            Attempt,
            AssessmentEvent,
            SubmissionEvent,
            GradeRecord,
            ConfidenceAssignment,
            ClientCaptureCheckpoint,
            ClientTimeSync,
            ResponseSnapshot,
            SimulationSnapshot,
            ResponseInputObservation,
        ):
            self.assertFalse(
                model.objects.filter(
                    **self._runtime_filter(model, self.target)
                ).exists(),
                model.__name__,
            )

        self.assertTrue(
            ExperimentSession.objects.filter(pk=self.other["session"].pk).exists()
        )
        self.assertTrue(
            RecordingCoverageSession.objects.filter(
                run=self.coverage_run, session=self.other["session"]
            ).exists()
        )
        self.assertTrue(
            RecordingCoverageAttempt.objects.filter(
                run=self.coverage_run, attempt=self.other["attempt"]
            ).exists()
        )
        self.assertTrue(os.path.exists(self.other_export["path"]))

        self.target_export["snapshot"].refresh_from_db()
        self.target_export["job"].refresh_from_db()
        self.assertIsNotNone(self.target_export["snapshot"].invalidated_at)
        self.assertEqual(
            self.target_export["snapshot"].invalidated_reason,
            "purge:collection_error",
        )
        self.assertIsNone(self.target_export["snapshot"].source_bundle)
        self.assertEqual(
            self.target_export["snapshot"].source_bundle_hash, "b" * 64
        )
        self.assertEqual(
            self.target_export["job"].status, ExportJob.Status.INVALIDATED
        )
        self.assertEqual(self.target_export["job"].artifact_path, "")
        self.assertEqual(self.target_export["job"].artifact_sha256, "a" * 64)
        self.assertFalse(os.path.exists(self.target_export["path"]))
        self.assertTrue(
            ExportJob.objects.filter(pk=self.target_export["job"].pk).exists()
        )

        for key in ("course", "bank", "question", "experiment", "version", "question_snapshot"):
            model = type(self.target[key])
            self.assertTrue(model.objects.filter(pk=self.target[key].pk).exists(), key)

        self.request.refresh_from_db()
        first_executed_at = self.request.executed_at
        first_counts = self.request.result_counts
        repeated = execute_purge_request(self.request, executed_by=self.teacher)
        self.request.refresh_from_db()
        self.assertEqual(repeated, first_counts)
        self.assertEqual(self.request.result_counts, first_counts)
        self.assertEqual(self.request.executed_at, first_executed_at)

        replacement = ExperimentSession.objects.create(
            version=self.target["version"],
            student=self.target_student,
            capture_contract=ensure_capture_contract_v2(),
        )
        self.assertNotEqual(replacement.pk, old_session_id)

    def test_failure_rolls_back_database_and_does_not_remove_export_file(self):
        with patch.object(
            DataPurgeRequest,
            "save",
            side_effect=RuntimeError("forced audit failure"),
        ):
            with self.assertRaisesMessage(RuntimeError, "forced audit failure"):
                execute_purge_request(self.request, executed_by=self.teacher)

        self.request.refresh_from_db()
        self.target_export["snapshot"].refresh_from_db()
        self.target_export["job"].refresh_from_db()
        self.assertEqual(self.request.status, DataPurgeRequest.Status.PREVIEW)
        self.assertIsNone(self.request.executed_at)
        self.assertTrue(
            ExperimentSession.objects.filter(pk=self.target["session"].pk).exists()
        )
        self.assertIsNone(self.target_export["snapshot"].invalidated_at)
        self.assertEqual(
            self.target_export["snapshot"].source_bundle,
            {"tables": {"attempts": [{"answer": "private"}]}},
        )
        self.assertEqual(
            self.target_export["job"].status, ExportJob.Status.SUCCEEDED
        )
        self.assertEqual(
            self.target_export["job"].artifact_path,
            self.target_export["path"],
        )
        self.assertTrue(os.path.exists(self.target_export["path"]))

    @staticmethod
    def _runtime_filter(model, runtime):
        if model is StudentQuestionAssignment:
            return {"session_id": runtime["session"].pk}
        if model in {Attempt, AssessmentEvent, ClientCaptureCheckpoint, ClientTimeSync}:
            return {"session_id": runtime["session"].pk}
        if model is GradeRecord:
            return {"submission__attempt_id": runtime["attempt"].pk}
        return {"attempt_id": runtime["attempt"].pk}
