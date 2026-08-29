import time
import uuid
from decimal import Decimal

from django.test import TestCase, tag
from django.utils import timezone

from apps.accounts.models import User
from apps.assessment.models import (
    AssessmentEvent,
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
    _assessment_event_append_scope,
)
from apps.assessment.recording_capture_services import ensure_capture_contract_v1
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .recording_coverage import generate_scope_coverage


@tag("performance")
class RecordingCoveragePerformanceTests(TestCase):
    def test_300_sessions_and_15000_events_finish_in_five_seconds(self):
        teacher = User.objects.create_user(
            username="coverage-performance-teacher", role=User.Role.TEACHER
        )
        students = User.objects.bulk_create(
            [
                User(
                    username=f"coverage-perf-{index:03d}",
                    student_number=f"CP{index:03d}",
                    role=User.Role.STUDENT,
                )
                for index in range(300)
            ]
        )
        bank = QuestionBank.objects.create(owner=teacher, name="Coverage 性能题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="Coverage 性能题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=teacher, question_bank=bank, name="Coverage 性能实验"
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
            text="Coverage 性能题目",
            options=[{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
            correct_answer="A",
        )
        item_instance = get_or_create_default_item_instance(snapshot)
        contract = ensure_capture_contract_v1()
        sessions = ExperimentSession.objects.bulk_create(
            [
                ExperimentSession(
                    version=version,
                    student=student,
                    capture_contract=contract,
                )
                for student in students
            ]
        )
        assignments = StudentQuestionAssignment.objects.bulk_create(
            [
                StudentQuestionAssignment(
                    session=session,
                    snapshot=snapshot,
                    item_instance=item_instance,
                    assigned_sequence_index=1,
                )
                for session in sessions
            ]
        )
        attempts = Attempt.objects.bulk_create(
            [Attempt(session=session, assignment=assignment)
             for session, assignment in zip(sessions, assignments)]
        )
        origin = timezone.now()
        events = []
        for session, assignment, attempt in zip(sessions, assignments, attempts):
            client_instance_id = uuid.uuid4()
            for sequence in range(1, 51):
                is_render = sequence == 1
                events.append(
                    AssessmentEvent(
                        event_domain=("assessment" if is_render else "telemetry"),
                        event_type=("item_rendered" if is_render else "document_visible"),
                        event_version=2 if is_render else 1,
                        envelope_version=2,
                        actor_source="client",
                        session=session,
                        student=session.student,
                        assignment=assignment,
                        attempt=attempt,
                        question_snapshot=snapshot,
                        item_instance=item_instance,
                        client_instance_id=client_instance_id,
                        client_sequence_no=sequence,
                        server_ingest_sequence_no=sequence,
                        client_wall_time_utc=origin,
                        client_monotonic_ms=Decimal(sequence * 100),
                        client_time_origin=origin,
                        server_received_at=origin,
                        server_committed_at=origin,
                        capture_mode="native",
                        quality_flags=[],
                        payload=(
                            {"presentation_state": "interactive"}
                            if is_render else {}
                        ),
                        app_version="performance-test",
                    )
                )
        with _assessment_event_append_scope():
            AssessmentEvent.objects.bulk_create(events, batch_size=1000)

        started = time.perf_counter()
        run = generate_scope_coverage(
            sessions,
            scope_type="performance_matrix",
            scope_definition={"matrix": "T30", "sessions": 300, "events": 15000},
            force=True,
        )
        elapsed = time.perf_counter() - started

        self.assertEqual(run.session_results.count(), 300)
        self.assertEqual(run.attempt_results.count(), 300)
        self.assertLess(
            elapsed,
            5.0,
            f"CoverageRun took {elapsed:.3f}s for 300 sessions / 15,000 events",
        )
