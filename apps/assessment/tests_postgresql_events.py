import threading
import uuid
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .event_services import append_assessment_event
from .models import (
    AssessmentEvent,
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class PostgreSQLAssessmentEventConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        teacher = User.objects.create_user(
            username="postgres-event-teacher",
            role=User.Role.TEACHER,
        )
        student = User.objects.create_user(
            username="POSTGRES-EVENT-S1",
            student_number="POSTGRES-EVENT-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=teacher, name="并发事件题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="并发事件题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=teacher,
            question_bank=bank,
            name="并发事件实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=teacher,
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="并发事件题目",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
        )
        item_instance = get_or_create_default_item_instance(snapshot)
        session = ExperimentSession.objects.create(version=version, student=student)
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        Attempt.objects.create(session=session, assignment=assignment)
        self.session_id = session.pk
        self.assignment_id = assignment.pk

    def _append_in_thread(self, *, barrier, client_instance_id, sequence, results):
        close_old_connections()
        try:
            assignment = StudentQuestionAssignment.objects.select_related(
                "session", "attempt", "snapshot", "item_instance"
            ).get(pk=self.assignment_id)
            barrier.wait(timeout=10)
            event = append_assessment_event(
                event_type="item_rendered",
                event_version=1,
                event_domain="assessment",
                actor_source="client",
                session=assignment.session,
                assignment=assignment,
                attempt=assignment.attempt,
                question_snapshot=assignment.snapshot,
                item_instance=assignment.item_instance,
                payload={},
                client_instance_id=client_instance_id,
                client_sequence_no=sequence,
            )
            results.append(("ok", event.event_id, event.server_ingest_sequence_no))
        except BaseException as error:
            results.append(("error", error))
        finally:
            close_old_connections()

    def _run_pair(self, client_events):
        barrier = threading.Barrier(2)
        results = []
        threads = [
            threading.Thread(
                target=self._append_in_thread,
                kwargs={
                    "barrier": barrier,
                    "client_instance_id": client_instance_id,
                    "sequence": sequence,
                    "results": results,
                },
            )
            for client_instance_id, sequence in client_events
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive(), "Concurrent event append deadlocked")
        errors = [result[1] for result in results if result[0] == "error"]
        self.assertEqual(errors, [])
        return results

    def test_same_session_concurrency_allocates_distinct_ingest_sequences(self):
        results = self._run_pair(
            [(uuid.uuid4(), 1), (uuid.uuid4(), 1)]
        )

        self.assertEqual(sorted(result[2] for result in results), [1, 2])
        self.assertEqual(AssessmentEvent.objects.count(), 2)

    def test_concurrent_identical_client_retry_persists_one_event(self):
        client_instance_id = uuid.uuid4()
        results = self._run_pair(
            [(client_instance_id, 7), (client_instance_id, 7)]
        )

        self.assertEqual(len({result[1] for result in results}), 1)
        self.assertEqual(AssessmentEvent.objects.count(), 1)
