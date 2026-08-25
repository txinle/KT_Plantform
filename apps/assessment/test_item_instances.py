from django.test import TestCase

from apps.accounts.models import User
from apps.experiments.models import (
    Experiment,
    ExperimentStudent,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.question_bank.models import Question, QuestionBank

from .services import get_or_create_session


class AssignmentItemInstanceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="assignment-instance-teacher",
            role=User.Role.TEACHER,
        )
        self.student_a = User.objects.create_user(
            username="ITEM-INSTANCE-S1",
            student_number="ITEM-INSTANCE-S1",
            role=User.Role.STUDENT,
        )
        self.student_b = User.objects.create_user(
            username="ITEM-INSTANCE-S2",
            student_number="ITEM-INSTANCE-S2",
            role=User.Role.STUDENT,
        )
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="作答呈现题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="静态呈现",
            correct_answer="A",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="作答呈现实验",
            status=Experiment.Status.PUBLISHED,
        )
        ExperimentStudent.objects.bulk_create(
            [
                ExperimentStudent(experiment=self.experiment, student=self.student_a),
                ExperimentStudent(experiment=self.experiment, student=self.student_b),
            ]
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
            configuration={"ordering_mode": Experiment.Ordering.FIXED},
        )
        self.snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=self.question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="静态呈现",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
            primary_kc="呈现 KC",
            all_kcs=["呈现 KC"],
        )

    def test_new_assignment_references_the_presented_item_instance(self):
        session = get_or_create_session(self.experiment, self.student_a)
        assignment = session.assignments.get()

        self.assertTrue(hasattr(assignment, "item_instance"))
        self.assertIsNotNone(assignment.item_instance_id)
        self.assertEqual(assignment.item_instance.question_snapshot, self.snapshot)
        self.assertEqual(assignment.item_instance.option_order, ["A", "B"])

    def test_static_presentations_reuse_the_same_default_instance(self):
        first_session = get_or_create_session(self.experiment, self.student_a)
        second_session = get_or_create_session(self.experiment, self.student_b)

        self.assertTrue(hasattr(first_session.assignments.get(), "item_instance_id"))
        self.assertEqual(
            first_session.assignments.get().item_instance_id,
            second_session.assignments.get().item_instance_id,
        )

    def test_assignment_item_instance_relation_is_non_nullable(self):
        field_names = {
            field.name: field
            for field in self.version.sessions.model._meta.apps.get_model(
                "assessment",
                "StudentQuestionAssignment",
            )._meta.fields
        }

        self.assertIn("item_instance", field_names)
        self.assertFalse(field_names["item_instance"].null)
