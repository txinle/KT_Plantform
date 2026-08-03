from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.accounts.models import User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from .models import Experiment, ExperimentQuestion, ExperimentStudent
from .services import publish_experiment

class PublishingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="S1", role=User.Role.STUDENT, student_number="S1")
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        self.question = Question.objects.create(bank=self.bank, sequence=1, question_type=Question.Type.SHORT, text="KT?", correct_answer="知识追踪")
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=self.bank, name="exp")
        ExperimentQuestion.objects.create(experiment=self.experiment, question=self.question, display_order=1)
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)

    def test_publish_rejects_question_without_primary_kc(self):
        with self.assertRaises(ValidationError):
            publish_experiment(self.experiment, self.teacher)

    def test_publish_copies_immutable_snapshot(self):
        concept = Concept.objects.create(owner=self.teacher, scope=Concept.Scope.PRIVATE, name="追踪", normalized_name="追踪")
        QuestionConcept.objects.create(question=self.question, concept=concept, is_primary=True)
        version = publish_experiment(self.experiment, self.teacher)
        snapshot = version.question_snapshots.get()
        self.question.text = "changed"
        self.question.save()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.text, "KT?")
        self.assertEqual(snapshot.primary_kc, "追踪")
