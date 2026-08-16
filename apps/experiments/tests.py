from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.analytics.export_fields import FIELD_LABELS
from apps.assessment.models import ExperimentSession
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion
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


class ExperimentResultStatusFilterTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", password="x", role=User.Role.TEACHER)
        self.other_teacher = User.objects.create_user(username="other", password="x", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name="状态实验")
        old_version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.latest_version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=2,
            published_by=self.teacher,
        )
        self.students = {}
        for index, (status, label) in enumerate(
            [
                (ExperimentSession.Status.IN_PROGRESS, "进行中学生"),
                (ExperimentSession.Status.COMPLETED, "已完成学生"),
                (ExperimentSession.Status.EXPIRED, "已超时学生"),
                (ExperimentSession.Status.INVALIDATED, "已作废学生"),
            ],
            start=1,
        ):
            student = User.objects.create_user(
                username=f"S{index}",
                student_number=f"S{index}",
                display_name=label,
                role=User.Role.STUDENT,
            )
            ExperimentSession.objects.create(version=self.latest_version, student=student, status=status)
            self.students[status] = label
        old_student = User.objects.create_user(
            username="OLD",
            student_number="OLD",
            display_name="旧版本学生",
            role=User.Role.STUDENT,
        )
        ExperimentSession.objects.create(version=old_version, student=old_student)
        self.client.force_login(self.teacher)
        self.url = reverse("experiments:results", args=[self.experiment.pk])

    def test_default_lists_only_latest_version_and_all_statuses(self):
        response = self.client.get(self.url)

        for label in self.students.values():
            self.assertContains(response, label)
        self.assertNotContains(response, "旧版本学生")
        self.assertContains(response, "4 条")

    def test_filters_each_session_status_and_preserves_selection(self):
        for status, expected_label in self.students.items():
            with self.subTest(status=status):
                response = self.client.get(self.url, {"status": status})
                self.assertContains(response, expected_label)
                for other_label in set(self.students.values()) - {expected_label}:
                    self.assertNotContains(response, other_label)
                self.assertContains(response, f'value="{status}" selected')
                self.assertContains(response, "1 条")

    def test_invalid_status_falls_back_to_all(self):
        response = self.client.get(self.url, {"status": "not-a-status"})

        for label in self.students.values():
            self.assertContains(response, label)
        self.assertNotContains(response, 'value="not-a-status"')

    def test_other_teacher_receives_404(self):
        self.client.force_login(self.other_teacher)

        self.assertEqual(self.client.get(self.url, {"status": "completed"}).status_code, 404)

    def test_results_page_makes_identified_exports_primary_and_keeps_legacy_links(self):
        response = self.client.get(self.url)
        html = response.content.decode("utf-8")

        identified_url = reverse("analytics:identified_export", args=[self.experiment.pk])
        navigation_url = reverse("analytics:identified_navigation_export", args=[self.experiment.pk])
        self.assertContains(response, f'action="{identified_url}"')
        self.assertContains(response, f'action="{navigation_url}"')
        self.assertContains(response, "实名可选字段 Excel")
        self.assertContains(response, "兼容导出")
        self.assertEqual(len(FIELD_LABELS), 29)
        self.assertEqual(html.count('type="checkbox" name="fields"'), 29)
        for field in FIELD_LABELS:
            self.assertContains(response, f'name="fields" value="{field}" checked')
        self.assertContains(response, reverse("analytics:kt_export", args=[self.experiment.pk]))
        self.assertContains(response, reverse("analytics:navigation_export", args=[self.experiment.pk]))
        self.assertContains(response, reverse("analytics:identity_export", args=[self.experiment.pk]))
