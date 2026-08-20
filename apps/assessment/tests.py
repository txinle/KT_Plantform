import uuid
from django.test import TestCase
from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from .models import Attempt
from .services import get_or_create_session, record_visit, save_draft, submit_attempt

class AssessmentFlowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="S1", role=User.Role.STUDENT, student_number="S1")
        bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        concept = Concept.objects.create(owner=self.teacher, scope=Concept.Scope.PRIVATE, name="加法", normalized_name="加法")
        question = Question.objects.create(bank=bank, sequence=1, question_type=Question.Type.SINGLE, text="2+2", correct_answer="B")
        QuestionOption.objects.create(question=question, key="A", text="3")
        QuestionOption.objects.create(question=question, key="B", text="4")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name="exp")
        ExperimentQuestion.objects.create(experiment=self.experiment, question=question, display_order=1)
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()

    def test_visit_draft_submit_and_idempotency(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.id, assignment.id)
        self.assertEqual(attempt.visit_count, 1)
        save_draft(attempt.id, "B")
        token = uuid.uuid4()
        submitted = submit_attempt(attempt.id, token)
        self.assertTrue(submitted.is_correct)
        self.assertEqual(submitted.submission_sequence_index, 1)
        repeated = submit_attempt(attempt.id, token)
        self.assertEqual(repeated.id, submitted.id)
        self.assertEqual(Attempt.objects.filter(status=Attempt.Status.SUBMITTED).count(), 1)
