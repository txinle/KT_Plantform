from django.test import TestCase

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from .models import Experiment, ExperimentQuestion, ExperimentStudent, QuestionSnapshot
from .services import publish_experiment


class CourseQuestionIdentitySnapshotTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="snapshot-identity-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="snapshot-identity-student",
            student_number="S001",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="原课程名称",
            code="ORIGINAL101",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="原题库名称",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=17,
            question_type=Question.Type.SHORT,
            text="快照题目",
            correct_answer="答案",
        )
        self.identity = allocate_course_question_identity(self.course, self.question)
        concept = Concept.objects.create(course=self.course, name="快照 KC")
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="快照实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
            class_snapshot_name="实验班",
        )

    def test_publish_freezes_course_question_identity_and_bank_metadata(self):
        self.assertTrue(hasattr(QuestionSnapshot, "course_sequence"))

        version = publish_experiment(self.experiment, self.teacher)
        snapshot = version.question_snapshots.get()
        self.course.name = "后续课程名称"
        self.course.code = "RENAMED101"
        self.course.save(update_fields=["name", "code"])
        self.bank.name = "后续题库名称"
        self.bank.save(update_fields=["name"])
        self.question.sequence = 88
        self.question.save(update_fields=["sequence"])
        version.refresh_from_db()
        snapshot.refresh_from_db()

        self.assertEqual(version.course_name_snapshot, "原课程名称")
        self.assertEqual(version.course_code_snapshot, "ORIGINAL101")
        self.assertEqual(snapshot.course_sequence, self.identity.course_sequence)
        self.assertEqual(snapshot.stable_code, self.identity.stable_code)
        self.assertEqual(snapshot.source_bank_id, self.bank.pk)
        self.assertEqual(snapshot.source_bank_name, "原题库名称")
        self.assertEqual(snapshot.bank_sequence, 17)
