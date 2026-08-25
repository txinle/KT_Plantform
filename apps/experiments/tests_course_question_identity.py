from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from .models import Experiment, ExperimentQuestion, QuestionSnapshot
from .services import publish_experiment, save_participant_rules


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
        self.class_group = ClassGroup.objects.create(course=self.course, name="快照实验班")
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
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
        save_participant_rules(self.experiment, [], [self.student], [])

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

    def test_course_publish_view_reports_missing_primary_kc_without_a_version(self):
        QuestionConcept.objects.filter(question=self.question, is_primary=True).delete()
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse(
                "courses:experiment_publish",
                args=[self.course.pk, self.experiment.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"以下题目缺少主知识点：{self.question.code}")
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_bulk_created_course_question_without_identity(self):
        bulk_question = Question(
            bank=self.bank,
            sequence=18,
            code="BULK-IDENTITY",
            question_type=Question.Type.SHORT,
            text="批量绕过模型保存的题目",
            correct_answer="答案",
        )
        Question.objects.bulk_create([bulk_question])
        bulk_question.refresh_from_db()
        concept = Concept.objects.create(course=self.course, name="批量题目 KC")
        QuestionConcept.objects.create(
            question=bulk_question,
            concept=concept,
            is_primary=True,
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=bulk_question,
            display_order=2,
        )

        with self.assertRaisesRegex(ValidationError, "BULK-IDENTITY"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(QuestionSnapshot.objects.count(), 0)
        self.assertFalse(self.experiment.versions.exists())
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)

    def test_publish_rejects_an_identity_mismatched_to_the_question_bank(self):
        other_course = Course.objects.create(
            owner=self.teacher,
            name="另一门课程",
            code="OTHER101",
            term="2026 秋",
        )
        other_bank = QuestionBank.objects.create(
            course=other_course,
            created_by=self.teacher,
            name="另一课程题库",
        )
        Question.objects.filter(pk=self.question.pk).update(bank=other_bank)

        with self.assertRaisesRegex(ValidationError, self.question.code):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(QuestionSnapshot.objects.count(), 0)
        self.assertFalse(self.experiment.versions.exists())
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
