from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Course, CourseEnrollment, ClassGroup, User
from apps.experiments.models import Experiment, ExperimentStudent

from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from .services.import_service import copy_shared_bank


class CourseQuestionBankScopeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="S1",
            student_number="S1",
            role=User.Role.STUDENT,
        )
        self.course_a = Course.objects.create(
            name="课程 A", code="A", term="2026春", owner=self.teacher
        )
        self.course_b = Course.objects.create(
            name="课程 B", code="B", term="2026春", owner=self.teacher
        )
        self.client.force_login(self.teacher)

    def test_same_concept_name_is_isolated_by_course(self):
        first = Concept.objects.create(course=self.course_a, name="函数")
        second = Concept.objects.create(course=self.course_b, name="函数")

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.normalized_name, second.normalized_name)

    def test_copy_shared_bank_clones_questions_options_and_course_concepts(self):
        source = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="共享题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        question = Question.objects.create(
            bank=source,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="1 + 1 = ?",
            correct_answer="B",
        )
        QuestionOption.objects.create(question=question, key="A", text="1")
        QuestionOption.objects.create(question=question, key="B", text="2")
        concept = Concept.objects.create(course=self.course_a, name="加法")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)

        copied = copy_shared_bank(source, self.course_b, self.teacher)

        self.assertEqual(copied.course, self.course_b)
        self.assertEqual(copied.created_by, self.teacher)
        copied_question = copied.questions.get()
        self.assertNotEqual(copied_question.pk, question.pk)
        self.assertEqual(copied_question.options.count(), 2)
        copied_concept = copied_question.primary_concept
        self.assertEqual(copied_concept.course, self.course_b)
        self.assertNotEqual(copied_concept.pk, concept.pk)

    def test_experiment_rejects_bank_or_student_from_another_course(self):
        bank_a = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="A 题库",
        )
        experiment = Experiment(
            course=self.course_b,
            created_by=self.teacher,
            question_bank=bank_a,
            name="错误实验",
        )
        with self.assertRaises(ValidationError):
            experiment.full_clean()

        class_a = ClassGroup.objects.create(course=self.course_a, name="一班")
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=class_a,
            student=self.student,
        )
        valid_bank = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            name="B 题库",
        )
        valid_experiment = Experiment.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            question_bank=valid_bank,
            name="B 实验",
        )
        link = ExperimentStudent(experiment=valid_experiment, student=self.student)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_course_bank_imports_questions_then_course_scoped_kc(self):
        bank = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="课程题库",
        )
        question_url = reverse("courses:question_import", args=[self.course_a.pk, bank.pk])
        question_file = SimpleUploadedFile(
            "questions.csv",
            "sequence,question,option_a,option_b,option_c,option_d,option_e,option_f,answer\n1,2+2=?,3,4,,,,,B\n".encode(),
            content_type="text/csv",
        )

        preview = self.client.post(question_url, {"file": question_file})
        self.assertContains(preview, "2+2=?")
        self.client.post(question_url, {"action": "confirm"})

        kc_url = reverse("courses:kc_import", args=[self.course_a.pk, bank.pk])
        kc_file = SimpleUploadedFile(
            "kcs.csv",
            "sequence,primary_kc,secondary_kcs\n1,加法,整数\n".encode(),
            content_type="text/csv",
        )
        preview = self.client.post(kc_url, {"file": kc_file})
        self.assertContains(preview, "加法")
        response = self.client.post(kc_url, {"action": "confirm"})

        self.assertRedirects(response, reverse("courses:bank_detail", args=[self.course_a.pk, bank.pk]))
        question = bank.questions.get(sequence=1)
        self.assertEqual(question.primary_concept.course, self.course_a)
        self.assertEqual(
            set(question.concept_links.values_list("concept__name", flat=True)),
            {"加法", "整数"},
        )
