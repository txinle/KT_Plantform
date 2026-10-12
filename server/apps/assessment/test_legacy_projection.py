from django.test import TestCase

from apps.assessment.legacy_projection import project_legacy_events
from apps.assessment.models import AssessmentEvent, ExperimentSession
from apps.assessment.services import get_or_create_session, record_visit, save_draft, submit_attempt
from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
import uuid


class LegacyProjectionTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="legacy-teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="legacy-student", role=User.Role.STUDENT, student_number="LEGACY-1")
        bank = QuestionBank.objects.create(owner=self.teacher, name="legacy-bank")
        question = Question.objects.create(bank=bank, sequence=1, question_type=Question.Type.SHORT, text="q", correct_answer="a")
        concept = Concept.objects.create(owner=self.teacher, scope=Concept.Scope.PRIVATE, name="kc", normalized_name="kc")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name="legacy-exp")
        ExperimentQuestion.objects.create(experiment=experiment, question=question, display_order=1)
        ExperimentStudent.objects.create(experiment=experiment, student=self.student)
        publish_experiment(experiment, self.teacher)
        self.experiment = Experiment.objects.get(pk=experiment.pk)

    def test_projection_is_idempotent_and_uses_distinct_legacy_events(self):
        session = get_or_create_session(self.experiment, self.student)
        attempt = record_visit(session.pk, session.assignments.first().pk)
        save_draft(attempt.pk, "a")
        submit_attempt(attempt.pk, uuid.uuid4())
        first = project_legacy_events(session_queryset=ExperimentSession.objects.filter(pk=session.pk))
        second = project_legacy_events(session_queryset=ExperimentSession.objects.filter(pk=session.pk))
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertTrue(AssessmentEvent.objects.filter(session=session, event_type__startswith="legacy_", capture_mode="legacy_projection").exists())
