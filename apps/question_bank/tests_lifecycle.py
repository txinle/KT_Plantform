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


class CourseBankListLifecycleTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="bank-lifecycle-teacher",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="题库生命周期课程",
            code="BANK-LIFE",
            term="2026春",
            owner=self.teacher,
        )
        self.active_bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="进行中题库",
        )
        self.archived_bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="已归档题库",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )
        self.client.force_login(self.teacher)

    def test_main_list_excludes_archived_banks_and_links_to_archive(self):
        response = self.client.get(reverse("courses:banks", args=[self.course.pk]))

        self.assertContains(response, self.active_bank.name)
        self.assertNotContains(response, self.archived_bank.name)
        self.assertContains(response, f"/courses/{self.course.pk}/banks/archived/")

    def test_archive_page_lists_only_archived_banks(self):
        response = self.client.get(
            f"/teacher/courses/{self.course.pk}/banks/archived/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.archived_bank.name)
        self.assertNotContains(response, self.active_bank.name)


class SharedBankEntryTests(TestCase):
    def setUp(self):
        self.target_teacher = User.objects.create_user(
            username="shared-target-teacher",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.source_teacher = User.objects.create_user(
            username="shared-source-teacher",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.target_course = Course.objects.create(
            name="目标课程",
            code="TARGET",
            term="2026春",
            owner=self.target_teacher,
        )
        self.source_course = Course.objects.create(
            name="共享来源课程",
            code="SOURCE",
            term="2026春",
            owner=self.source_teacher,
        )
        self.shared_bank = QuestionBank.objects.create(
            course=self.source_course,
            created_by=self.source_teacher,
            name="分层共享题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        self.client.force_login(self.target_teacher)

    def test_course_bank_list_links_to_shared_browser_without_flat_bank_rows(self):
        response = self.client.get(
            reverse("courses:banks", args=[self.target_course.pk])
        )

        self.assertNotContains(response, self.shared_bank.name)
        self.assertContains(
            response,
            f"/courses/{self.target_course.pk}/banks/shared/",
        )

    def test_shared_browser_drills_from_course_to_creator_to_bank(self):
        sources = self.client.get(
            f"/teacher/courses/{self.target_course.pk}/banks/shared/"
        )
        creators = self.client.get(
            f"/teacher/courses/{self.target_course.pk}/banks/shared/{self.source_course.pk}/"
        )
        banks = self.client.get(
            f"/teacher/courses/{self.target_course.pk}/banks/shared/"
            f"{self.source_course.pk}/creators/{self.source_teacher.pk}/"
        )

        self.assertEqual(sources.status_code, 200)
        self.assertContains(sources, self.source_course.name)
        self.assertNotContains(sources, self.shared_bank.name)
        self.assertEqual(creators.status_code, 200)
        self.assertContains(creators, self.source_teacher.shown_name)
        self.assertNotContains(creators, self.shared_bank.name)
        self.assertEqual(banks.status_code, 200)
        self.assertContains(banks, self.shared_bank.name)
        self.assertContains(
            banks,
            reverse(
                "courses:copy_bank",
                args=[self.target_course.pk, self.shared_bank.pk],
            ),
        )


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

    def test_collaborator_can_delete_unreferenced_bank_and_identity_is_retained(self):
        identity_pk = self.question.course_identity.pk
        self.client.force_login(self.collaborator)

        response = self.client.post(
            self.bank_delete_url(),
            {"confirmation": "delete"},
        )

        self.assertRedirects(response, reverse("courses:banks", args=[self.course.pk]))
        self.assertFalse(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        identity = CourseQuestionIdentity.objects.get(pk=identity_pk)
        self.assertIsNone(identity.question_id)
        self.assertTrue(
            AuditLog.objects.filter(action="question_bank.delete").exists()
        )

    def test_bank_referenced_by_experiment_is_not_deleted(self):
        Experiment.objects.create(
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

        self.assertRedirects(
            response,
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )
        self.assertContains(response, "题库已被实验或发布快照引用，不能永久删除")
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())

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

    def test_unreferenced_question_deletes_without_reordering_and_keeps_identity(self):
        second = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="保留题目",
            correct_answer="保留答案",
        )
        identity_pk = self.question.course_identity.pk
        second_identity = second.course_identity.stable_code
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
        self.assertIsNone(
            CourseQuestionIdentity.objects.get(pk=identity_pk).question_id
        )
        second.refresh_from_db()
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.course_identity.stable_code, second_identity)
        self.assertTrue(AuditLog.objects.filter(action="question.delete").exists())

    def test_question_used_by_draft_experiment_is_not_deleted(self):
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

        self.assertContains(response, "题目已被实验或发布快照引用，不能永久删除")
        self.assertTrue(Question.objects.filter(pk=self.question.pk).exists())

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

        self.assertContains(response, "题目已被实验或发布快照引用，不能永久删除")
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


class QuestionBankLifecycleUITests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="bank-lifecycle-ui-teacher",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="题库生命周期界面课程",
            code="BANK-UI",
            term="2026春",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="界面测试题库",
        )
        self.first = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="第一道界面题",
            correct_answer="答案一",
        )
        self.second = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="第二道界面题",
            correct_answer="答案二",
        )
        self.client.force_login(self.teacher)

    def test_base_template_busts_cache_for_lifecycle_javascript(self):
        response = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )

        self.assertContains(response, "static/js/app.js?v=20260825.1")

    def test_main_list_uses_dedicated_archive_action_and_disallows_archived_creation(self):
        response = self.client.get(reverse("courses:banks", args=[self.course.pk]))
        html = response.content.decode()

        self.assertContains(
            response,
            reverse("courses:bank_archive", args=[self.course.pk, self.bank.pk]),
        )
        self.assertNotIn('<option value="archived"', html)

        rejected = self.client.post(
            reverse("courses:banks", args=[self.course.pk]),
            {
                "name": "不应直接创建的归档题库",
                "description": "",
                "visibility": QuestionBank.Visibility.ARCHIVED,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(
            QuestionBank.objects.filter(name="不应直接创建的归档题库").exists()
        )

    def test_bank_detail_exposes_bank_and_single_question_confirmation_dialogs(self):
        response = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )
        html = response.content.decode()

        self.assertContains(response, "归档题库")
        self.assertContains(response, "确认永久删除题库")
        self.assertContains(
            response,
            reverse("courses:bank_delete", args=[self.course.pk, self.bank.pk]),
        )
        self.assertContains(response, "确认永久删除题目")
        self.assertEqual(html.count('id="delete-question-dialog"'), 1)
        for question in (self.first, self.second):
            self.assertContains(
                response,
                reverse(
                    "courses:question_delete",
                    args=[self.course.pk, self.bank.pk, question.pk],
                ),
            )
        self.assertContains(response, 'data-question-delete-trigger')
        self.assertContains(response, 'name="confirmation" value="delete"')

    def test_archived_bank_detail_and_archive_page_offer_restore_without_question_delete(self):
        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility"])
        restore_url = reverse(
            "courses:bank_restore", args=[self.course.pk, self.bank.pk]
        )

        detail = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )
        archive = self.client.get(
            reverse("courses:archived_banks", args=[self.course.pk])
        )

        self.assertContains(detail, restore_url)
        self.assertNotContains(detail, 'data-question-delete-trigger')
        self.assertContains(archive, restore_url)

    def test_archived_course_hides_all_lifecycle_mutations(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        detail = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )

        self.assertNotContains(
            detail,
            reverse("courses:bank_archive", args=[self.course.pk, self.bank.pk]),
        )
        self.assertNotContains(
            detail,
            reverse("courses:bank_delete", args=[self.course.pk, self.bank.pk]),
        )
        self.assertNotContains(detail, 'data-question-delete-trigger')
