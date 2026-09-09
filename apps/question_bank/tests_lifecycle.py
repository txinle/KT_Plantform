from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Course, CourseTeacher, User
from apps.audit.models import AuditLog
from apps.experiments.models import (
    Experiment,
    ExperimentQuestion,
    ExperimentVersion,
    QuestionSnapshot,
)

from .models import (
    Concept,
    CourseQuestionIdentity,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)
from .services.import_service import copy_shared_bank






class SharedBankCopyNamingTests(TestCase):
    def test_copy_keeps_source_name_and_content_independent(self):
        source_teacher = User.objects.create_user(
            username="copy-source-teacher",
            role=User.Role.TEACHER,
        )
        target_teacher = User.objects.create_user(
            username="copy-target-teacher",
            role=User.Role.TEACHER,
        )
        source_course = Course.objects.create(
            name="复制来源课程",
            code="COPY-SOURCE",
            term="2026春",
            owner=source_teacher,
        )
        target_course = Course.objects.create(
            name="复制目标课程",
            code="COPY-TARGET",
            term="2026春",
            owner=target_teacher,
        )
        source = QuestionBank.objects.create(
            course=source_course,
            created_by=source_teacher,
            name="原始共享题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        source_question = Question.objects.create(
            bank=source,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="原始题干",
            correct_answer="原始答案",
        )

        copied = copy_shared_bank(source, target_course, target_teacher)
        source_question.text = "已修改的源题干"
        source_question.save(update_fields=["text"])

        self.assertEqual(copied.name, source.name)
        self.assertEqual(copied.questions.get(sequence=1).text, "原始题干")
        self.assertEqual(copied.visibility, QuestionBank.Visibility.PRIVATE)


class BankArchiveRestoreTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="archive-restore-teacher",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="归档恢复课程",
            code="ARCHIVE-RESTORE",
            term="2026春",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="待归档题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        self.client.force_login(self.teacher)

    def test_archive_and_restore_are_audited_and_restore_as_private(self):
        archive = self.client.post(
            f"/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/archive/"
        )
        self.bank.refresh_from_db()

        self.assertRedirects(archive, reverse("courses:banks", args=[self.course.pk]))
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)
        self.assertTrue(AuditLog.objects.filter(action="question_bank.archive").exists())

        restore = self.client.post(
            f"/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/restore/"
        )
        self.bank.refresh_from_db()

        self.assertRedirects(restore, reverse("courses:banks", args=[self.course.pk]))
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)
        self.assertTrue(AuditLog.objects.filter(action="question_bank.restore").exists())

    def test_archived_course_cannot_archive_or_restore_banks(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        archive = self.client.post(
            f"/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/archive/"
        )
        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility"])
        restore = self.client.post(
            f"/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/restore/"
        )

        self.assertEqual(archive.status_code, 403)
        self.assertEqual(restore.status_code, 403)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)

    def test_archive_and_restore_are_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)

        self.assertEqual(
            csrf_client.post(
                f"/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/archive/"
            ).status_code,
            403,
        )

    def test_visibility_shortcut_cannot_archive_bank(self):
        response = self.client.post(
            reverse("courses:bank_visibility", args=[self.course.pk, self.bank.pk]),
            {"visibility": QuestionBank.Visibility.ARCHIVED},
        )

        self.assertRedirects(response, reverse("courses:banks", args=[self.course.pk]))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

    def test_metadata_form_cannot_bypass_archive_or_restore_transitions(self):
        update_url = reverse(
            "courses:bank_update", args=[self.course.pk, self.bank.pk]
        )
        archive_attempt = self.client.post(
            update_url,
            {
                "name": self.bank.name,
                "description": self.bank.description,
                "visibility": QuestionBank.Visibility.ARCHIVED,
            },
        )
        self.assertEqual(archive_attempt.status_code, 200)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility"])
        shared_restore_attempt = self.client.post(
            update_url,
            {
                "name": self.bank.name,
                "description": self.bank.description,
                "visibility": QuestionBank.Visibility.SHARED,
            },
        )
        self.assertEqual(shared_restore_attempt.status_code, 200)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)


class QuestionBankDeletionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="bank-delete-owner",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="bank-delete-collaborator",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="bank-delete-outsider",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="题库删除课程",
            code="BANK-DELETE",
            term="2026春",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.owner,
            name="可删除题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="待删除题目",
            correct_answer="A",
        )
        QuestionOption.objects.create(
            question=self.question,
            key="A",
            text="正确选项",
        )
        self.concept = Concept.objects.create(course=self.course, name="删除测试 KC")
        QuestionConcept.objects.create(
            question=self.question,
            concept=self.concept,
            is_primary=True,
        )

    def bank_delete_url(self, bank=None):
        return (
            f"/teacher/courses/{self.course.pk}/banks/"
            f"{(bank or self.bank).pk}/delete/"
        )

    def question_delete_url(self, question=None, bank=None):
        selected_bank = bank or self.bank
        selected_question = question or self.question
        return (
            f"/teacher/courses/{self.course.pk}/banks/{selected_bank.pk}/"
            f"questions/{selected_question.pk}/delete/"
        )

    def test_bank_delete_requires_confirmation(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.bank_delete_url())

        self.assertEqual(response.status_code, 400)
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())

    def test_collaborator_can_delete_unreferenced_bank_and_identity_is_removed(self):
        identity_pk = self.question.course_identity.pk
        self.client.force_login(self.collaborator)

        response = self.client.post(
            self.bank_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertRedirects(response, reverse("courses:banks", args=[self.course.pk]))
        self.assertFalse(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertFalse(CourseQuestionIdentity.objects.filter(pk=identity_pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(action="question_bank.delete").exists()
        )

    def test_bank_referenced_only_by_draft_experiment_is_deleted_and_draft_is_preserved(self):
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.owner,
            question_bank=self.bank,
            name="引用题库的实验",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            self.bank_delete_url(),
            {"confirmation": "delete"},
            follow=True,
        )

        self.assertRedirects(response, reverse("courses:banks", args=[self.course.pk]))
        self.assertFalse(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        experiment.refresh_from_db()
        self.assertIsNone(experiment.question_bank_id)

    def test_outsider_cannot_delete_bank(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            self.bank_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())

    def test_bank_delete_is_csrf_protected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)

        response = csrf_client.post(
            self.bank_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 403)

    def test_bank_delete_rolls_back_when_audit_fails(self):
        self.client.force_login(self.owner)

        with patch(
            "apps.question_bank.course_views.log_action",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    self.bank_delete_url(),
                    {"confirmation": "delete"},
                )

        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertTrue(Question.objects.filter(pk=self.question.pk).exists())

    def test_unreferenced_question_deletes_and_compacts_course_identity(self):
        second = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="保留题目",
            correct_answer="保留答案",
        )
        identity_pk = self.question.course_identity.pk
        second_identity_pk = second.course_identity.pk
        self.client.force_login(self.collaborator)

        response = self.client.post(
            self.question_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertRedirects(
            response,
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )
        self.assertFalse(Question.objects.filter(pk=self.question.pk).exists())
        self.assertFalse(CourseQuestionIdentity.objects.filter(pk=identity_pk).exists())
        second.refresh_from_db()
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.course_identity.pk, second_identity_pk)
        self.assertEqual(second.course_identity.course_sequence, 1)
        self.assertEqual(
            second.course_identity.stable_code,
            f"C{self.course.pk:06d}-Q000001",
        )
        self.assertTrue(AuditLog.objects.filter(action="question.delete").exists())

    def test_question_used_only_by_draft_experiment_is_deleted_and_detached(self):
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.owner,
            question_bank=self.bank,
            name="引用单题的草稿实验",
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=self.question,
            display_order=1,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            self.question_delete_url(),
            {"confirmation": "delete"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Question.objects.filter(pk=self.question.pk).exists())
        self.assertFalse(ExperimentQuestion.objects.filter(experiment=experiment).exists())

    def test_question_used_by_published_snapshot_is_not_deleted(self):
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.owner,
            question_bank=self.bank,
            name="已发布实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.owner,
        )
        QuestionSnapshot.objects.create(
            version=version,
            source_question=self.question,
            source_sequence=self.question.sequence,
            code=self.question.code,
            question_type=self.question.question_type,
            text=self.question.text,
            correct_answer=self.question.correct_answer,
            primary_kc="删除测试 KC",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            self.question_delete_url(),
            {"confirmation": "delete"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Question.objects.filter(pk=self.question.pk).exists())

    def test_archived_bank_question_cannot_be_deleted(self):
        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility"])
        self.client.force_login(self.owner)

        response = self.client.post(
            self.question_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Question.objects.filter(pk=self.question.pk).exists())
