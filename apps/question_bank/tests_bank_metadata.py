from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.course_access import lock_writable_course
from apps.accounts.models import Course, CourseTeacher, User
from apps.audit.models import AuditLog

from .models import Concept, Question, QuestionBank, QuestionConcept
from .services.import_parsing import ParsedKCMapping, ParsedQuestion
from .services.import_service import import_kc_mappings, import_questions


class CourseBankMetadataEditTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="course-owner", role=User.Role.TEACHER)
        self.bank_owner = User.objects.create_user(username="bank-owner", role=User.Role.TEACHER)
        self.collaborator = User.objects.create_user(username="collaborator", role=User.Role.TEACHER)
        self.outsider = User.objects.create_user(username="outsider", role=User.Role.TEACHER)
        self.admin = User.objects.create_user(username="metadata-admin", role=User.Role.ADMIN)
        self.superuser = User.objects.create_user(
            username="metadata-superuser",
            role=User.Role.ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.course = Course.objects.create(
            name="研究方法",
            code="RM101",
            term="2026 秋",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.bank_owner)
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.bank_owner,
            name="原始题库名称",
            description="原始题库说明",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )
        self.edit_url = reverse("courses:bank_edit", args=[self.course.pk, self.bank.pk])
        self.update_url = reverse("courses:bank_update", args=[self.course.pk, self.bank.pk])
        self.visibility_url = reverse(
            "courses:bank_visibility", args=[self.course.pk, self.bank.pk]
        )

    def metadata_payload(self, **overrides):
        payload = {
            "name": "修订后题库名称",
            "description": "修订后题库说明",
            "visibility": QuestionBank.Visibility.PRIVATE,
        }
        payload.update(overrides)
        return payload

    def test_course_owner_can_edit_archived_bank_metadata_and_audit_excludes_contents(self):
        self.client.force_login(self.owner)

        detail = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )
        self.assertContains(detail, self.edit_url)
        self.assertContains(detail, "编辑题库")

        page = self.client.get(self.edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'action="{self.update_url}"')
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        for field_name in ("name", "description", "visibility"):
            self.assertContains(page, f'name="{field_name}"')

        response = self.client.post(self.update_url, self.metadata_payload())

        self.assertRedirects(
            response,
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "修订后题库名称")
        self.assertEqual(self.bank.description, "修订后题库说明")
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)
        audit = AuditLog.objects.get(action="question_bank.update", object_id=str(self.bank.pk))
        self.assertEqual(
            audit.details,
            {
                "bank_id": self.bank.pk,
                "course_id": self.course.pk,
                "changed_fields": ["name", "description", "visibility"],
            },
        )
        self.assertNotIn("修订后题库名称", str(audit.details))
        self.assertNotIn("修订后题库说明", str(audit.details))

    def test_invalid_metadata_form_returns_accessible_error_feedback_without_writing(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.update_url, self.metadata_payload(name=""))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请检查以下内容")
        self.assertContains(response, "题库名称")
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "原始题库名称")

    def test_course_collaborator_can_edit_bank_metadata(self):
        self.client.force_login(self.collaborator)

        allowed = self.client.post(self.update_url, self.metadata_payload())
        quick_allowed = self.client.post(
            self.visibility_url,
            {"visibility": QuestionBank.Visibility.SHARED},
        )

        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(quick_allowed.status_code, 302)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)
        self.assertEqual(self.bank.name, "修订后题库名称")

        self.client.force_login(self.bank_owner)
        allowed = self.client.post(self.update_url, self.metadata_payload())
        self.assertRedirects(
            allowed,
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )

        self.client.force_login(self.outsider)
        outside = self.client.get(self.edit_url)
        self.assertEqual(outside.status_code, 404)

    def test_metadata_edit_entry_is_visible_only_to_authorized_metadata_editors(self):
        cases = (
            (self.owner, True),
            (self.bank_owner, True),
            (self.admin, True),
            (self.superuser, True),
            (self.collaborator, True),
        )

        for user, can_edit in cases:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
                )

                self.assertEqual(response.context["can_edit_bank_metadata"], can_edit)
                if can_edit:
                    self.assertContains(response, self.edit_url)
                else:
                    self.assertNotContains(response, self.edit_url)

    def test_metadata_routes_are_method_split_csrf_protected_and_course_archiving_still_wins(self):
        self.client.force_login(self.owner)

        self.assertEqual(self.client.post(self.edit_url, self.metadata_payload()).status_code, 405)
        self.assertEqual(self.client.get(self.update_url).status_code, 405)
        self.assertEqual(self.client.put(self.update_url, self.metadata_payload()).status_code, 405)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "原始题库名称")

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        csrf_denied = csrf_client.post(self.update_url, self.metadata_payload())
        self.assertEqual(csrf_denied.status_code, 403)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "原始题库名称")

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        archived_course = self.client.post(self.update_url, self.metadata_payload())
        archived_course_quick = self.client.post(
            self.visibility_url,
            {"visibility": QuestionBank.Visibility.PRIVATE},
        )
        self.assertEqual(archived_course.status_code, 403)
        self.assertEqual(archived_course_quick.status_code, 403)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "原始题库名称")
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)

    def test_stale_active_course_cannot_update_bank_metadata_after_archive(self):
        self.client.force_login(self.owner)
        stale_course = Course.objects.get(pk=self.course.pk)
        Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)

        with patch(
            "apps.question_bank.course_views.get_accessible_course",
            return_value=stale_course,
        ):
            response = self.client.post(self.update_url, self.metadata_payload())

        self.assertEqual(response.status_code, 403)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "原始题库名称")
        self.assertEqual(self.bank.description, "原始题库说明")
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)

    def test_course_lock_rechecks_a_stale_active_course(self):
        stale_course = Course.objects.get(pk=self.course.pk)
        Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)

        with transaction.atomic():
            with self.assertRaises(PermissionDenied):
                lock_writable_course(stale_course)

    def test_visibility_shortcut_remains_post_only(self):
        self.client.force_login(self.owner)

        self.assertEqual(self.client.get(self.visibility_url).status_code, 405)

    def test_visibility_shortcut_preserves_redirect_and_uses_privacy_safe_audit_details(self):
        self.bank.visibility = QuestionBank.Visibility.PRIVATE
        self.bank.save(update_fields=["visibility"])
        self.client.force_login(self.owner)

        response = self.client.post(
            self.visibility_url,
            {"visibility": QuestionBank.Visibility.SHARED},
        )

        self.assertRedirects(response, reverse("courses:banks", args=[self.course.pk]))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)
        audit = AuditLog.objects.get(action="question_bank.visibility", object_id=str(self.bank.pk))
        self.assertEqual(
            audit.details,
            {
                "bank_id": self.bank.pk,
                "course_id": self.course.pk,
                "changed_fields": ["visibility"],
            },
        )
        self.assertNotIn("shared", str(audit.details))


class ArchivedBankStaleServiceWriteTests(TestCase):
    archived_write_message = "题库已归档，不能写入题目内容或 KC 映射。"

    def setUp(self):
        self.teacher = User.objects.create_user(username="stale-bank-service-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="服务层锁定题库")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="已有题目",
            correct_answer="已有答案",
        )

    def archive_after_loading_a_stale_bank(self):
        stale_bank = QuestionBank.objects.get(pk=self.bank.pk)
        self.assertEqual(stale_bank.visibility, QuestionBank.Visibility.PRIVATE)
        QuestionBank.objects.filter(pk=self.bank.pk).update(
            visibility=QuestionBank.Visibility.ARCHIVED
        )
        return stale_bank

    def test_question_import_rechecks_a_stale_bank_inside_its_transaction(self):
        stale_bank = self.archive_after_loading_a_stale_bank()
        parsed_question = ParsedQuestion(
            sequence=2,
            text="不应导入",
            options={"A": "甲", "B": "乙"},
            answer="A",
            question_type=Question.Type.SINGLE,
        )

        with self.assertRaisesMessage(PermissionDenied, self.archived_write_message):
            import_questions(stale_bank, [parsed_question])

        self.assertFalse(self.bank.questions.filter(sequence=2).exists())

    def test_kc_import_rechecks_a_stale_bank_inside_its_transaction(self):
        stale_bank = self.archive_after_loading_a_stale_bank()
        mapping = ParsedKCMapping(sequence=1, primary="不应创建 KC", secondary=[])

        with self.assertRaisesMessage(PermissionDenied, self.archived_write_message):
            import_kc_mappings(stale_bank, self.teacher, [mapping])

        self.assertFalse(QuestionConcept.objects.filter(question=self.question).exists())
        self.assertFalse(Concept.objects.filter(owner=self.teacher, name="不应创建 KC").exists())


class ArchivedCourseBankContentWriteTests(TestCase):
    archived_write_message = "题库已归档，不能写入题目内容或 KC 映射。"

    def setUp(self):
        self.teacher = User.objects.create_user(username="archived-teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(
            name="归档题库课程",
            code="ARCH101",
            term="2026 秋",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="归档历史题库",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="历史题干",
            correct_answer="历史答案",
        )
        self.concept = Concept.objects.create(course=self.course, name="历史 KC")
        QuestionConcept.objects.create(question=self.question, concept=self.concept, is_primary=True)
        self.client.force_login(self.teacher)
        self.course_question_import_url = reverse(
            "courses:question_import", args=[self.course.pk, self.bank.pk]
        )
        self.course_kc_import_url = reverse(
            "courses:kc_import", args=[self.course.pk, self.bank.pk]
        )
        self.course_question_edit_url = reverse(
            "courses:question_edit", args=[self.course.pk, self.bank.pk, self.question.pk]
        )
        self.legacy_question_import_url = reverse("question_bank:question_import", args=[self.bank.pk])
        self.legacy_kc_import_url = reverse("question_bank:kc_import", args=[self.bank.pk])
        self.legacy_question_edit_url = reverse(
            "question_bank:question_edit", args=[self.bank.pk, self.question.pk]
        )

    def content_snapshot(self):
        return {
            "questions": list(
                self.bank.questions.values(
                    "id", "sequence", "text", "correct_answer", "question_type", "status"
                )
            ),
            "links": list(
                QuestionConcept.objects.filter(question__bank=self.bank).values(
                    "question_id", "concept_id", "is_primary"
                )
            ),
            "concepts": list(
                Concept.objects.filter(course=self.course).values("id", "name", "normalized_name")
            ),
            "audit_count": AuditLog.objects.count(),
        }

    def assert_archived_write_denied(self, response, before):
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, self.archived_write_message, status_code=403)
        self.assertEqual(self.content_snapshot(), before)

    def question_file(self):
        return SimpleUploadedFile(
            "new-questions.csv",
            (
                "sequence,question,option_a,option_b,option_c,option_d,option_e,option_f,answer\n"
                "2,新题目,甲,乙,,,,,B\n"
            ).encode(),
            content_type="text/csv",
        )

    def kc_file(self):
        return SimpleUploadedFile(
            "new-kcs.csv",
            "sequence,primary_kc,secondary_kcs\n1,替换 KC,次级 KC\n".encode(),
            content_type="text/csv",
        )

    def question_edit_payload(self):
        return {
            "text": "不应写入的题干",
            "question_type": Question.Type.SHORT,
            "status": Question.Status.ACTIVE,
            "correct_answer_text": "不应写入的答案",
            "accepted_answers_text": "",
            "primary_concept": self.concept.pk,
        }

    def test_archived_bank_detail_is_readable_but_all_course_and_legacy_content_forms_are_forbidden(self):
        detail = self.client.get(
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "题库已归档")
        self.assertContains(detail, "历史题干")

        for url in (
            self.course_question_import_url,
            self.course_kc_import_url,
            self.course_question_edit_url,
            self.legacy_question_import_url,
            self.legacy_kc_import_url,
            self.legacy_question_edit_url,
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, self.archived_write_message, status_code=403)

    def test_question_import_preview_and_confirm_are_forbidden_without_writing(self):
        before = self.content_snapshot()
        for url in (self.course_question_import_url, self.legacy_question_import_url):
            with self.subTest(url=url):
                self.assert_archived_write_denied(
                    self.client.post(url, {"file": self.question_file()}), before
                )

        session = self.client.session
        session[f"course_question_import:{self.course.pk}:{self.bank.pk}"] = [
            {
                "sequence": 2,
                "text": "确认导入也不应写入",
                "options": {"A": "甲", "B": "乙"},
                "answer": "B",
                "question_type": Question.Type.SINGLE,
            }
        ]
        session.save()
        self.assert_archived_write_denied(
            self.client.post(self.course_question_import_url, {"action": "confirm"}), before
        )

    def test_kc_import_preview_and_confirm_are_forbidden_without_writing(self):
        before = self.content_snapshot()
        for url in (self.course_kc_import_url, self.legacy_kc_import_url):
            with self.subTest(url=url):
                self.assert_archived_write_denied(
                    self.client.post(url, {"file": self.kc_file()}), before
                )

        session = self.client.session
        session[f"course_kc_import:{self.course.pk}:{self.bank.pk}"] = [
            {"sequence": 1, "primary": "确认替换也不应写入", "secondary": []}
        ]
        session.save()
        self.assert_archived_write_denied(
            self.client.post(self.course_kc_import_url, {"action": "confirm"}), before
        )

    def test_question_edit_is_forbidden_on_course_and_legacy_routes_without_writing(self):
        before = self.content_snapshot()
        for url in (self.course_question_edit_url, self.legacy_question_edit_url):
            with self.subTest(url=url):
                self.assert_archived_write_denied(
                    self.client.post(url, self.question_edit_payload()), before
                )


class ArchivedLegacyBankContentWriteTests(TestCase):
    archived_write_message = "题库已归档，不能写入题目内容或 KC 映射。"

    def setUp(self):
        self.teacher = User.objects.create_user(username="legacy-archived-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="旧版归档题库",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="旧版历史题干",
            correct_answer="旧版历史答案",
        )
        self.concept = Concept.objects.create(owner=self.teacher, name="旧版历史 KC")
        QuestionConcept.objects.create(question=self.question, concept=self.concept, is_primary=True)
        self.client.force_login(self.teacher)
        self.question_import_url = reverse("question_bank:question_import", args=[self.bank.pk])
        self.kc_import_url = reverse("question_bank:kc_import", args=[self.bank.pk])
        self.question_edit_url = reverse(
            "question_bank:question_edit", args=[self.bank.pk, self.question.pk]
        )

    def content_snapshot(self):
        return {
            "questions": list(self.bank.questions.values("id", "sequence", "text", "correct_answer")),
            "links": list(
                QuestionConcept.objects.filter(question__bank=self.bank).values(
                    "question_id", "concept_id", "is_primary"
                )
            ),
            "concepts": list(Concept.objects.filter(owner=self.teacher).values("id", "name")),
        }

    def assert_denied_without_writes(self, response, before):
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, self.archived_write_message, status_code=403)
        self.assertEqual(self.content_snapshot(), before)

    def test_archived_legacy_bank_cannot_write_through_direct_content_routes(self):
        detail = self.client.get(reverse("question_bank:bank_detail", args=[self.bank.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "旧版历史题干")

        before = self.content_snapshot()
        for url in (self.question_import_url, self.kc_import_url, self.question_edit_url):
            with self.subTest(method="GET", url=url):
                self.assert_denied_without_writes(self.client.get(url), before)

        question_file = SimpleUploadedFile(
            "legacy-questions.csv",
            (
                "sequence,question,option_a,option_b,option_c,option_d,option_e,option_f,answer\n"
                "2,不能写入,甲,乙,,,,,B\n"
            ).encode(),
            content_type="text/csv",
        )
        self.assert_denied_without_writes(
            self.client.post(self.question_import_url, {"file": question_file}), before
        )
        kc_file = SimpleUploadedFile(
            "legacy-kcs.csv",
            "sequence,primary_kc,secondary_kcs\n1,不能替换,次级\n".encode(),
            content_type="text/csv",
        )
        self.assert_denied_without_writes(
            self.client.post(self.kc_import_url, {"file": kc_file}), before
        )
        self.assert_denied_without_writes(
            self.client.post(
                self.question_edit_url,
                {
                    "text": "不能写入",
                    "question_type": Question.Type.SHORT,
                    "status": Question.Status.ACTIVE,
                    "correct_answer_text": "不能写入",
                    "accepted_answers_text": "",
                },
            ),
            before,
        )

        session = self.client.session
        session[f"question_import_{self.bank.pk}"] = [
            {
                "sequence": 2,
                "text": "确认也不能写入",
                "options": {"A": "甲", "B": "乙"},
                "answer": "B",
                "question_type": Question.Type.SINGLE,
            }
        ]
        session[f"kc_import_{self.bank.pk}"] = [
            {"sequence": 1, "primary": "确认也不能替换", "secondary": []}
        ]
        session.save()
        self.assert_denied_without_writes(
            self.client.post(self.question_import_url, {"action": "confirm"}), before
        )
        self.assert_denied_without_writes(
            self.client.post(self.kc_import_url, {"action": "confirm"}), before
        )


class LegacyVisibilityAuditPrivacyTests(TestCase):
    def test_legacy_visibility_update_keeps_its_redirect_and_records_only_identifiers_and_changed_fields(self):
        teacher = User.objects.create_user(username="legacy-audit-teacher", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(
            owner=teacher,
            name="不应出现在审计中的题库名称",
            description="不应出现在审计中的题库说明",
            visibility=QuestionBank.Visibility.PRIVATE,
        )
        self.client.force_login(teacher)

        response = self.client.post(
            reverse("question_bank:bank_visibility_update", args=[bank.pk]),
            {"visibility": QuestionBank.Visibility.SHARED},
        )

        self.assertRedirects(response, reverse("question_bank:bank_list"))
        audit = AuditLog.objects.get(action="question_bank.visibility", object_id=str(bank.pk))
        self.assertEqual(
            audit.details,
            {
                "bank_id": bank.pk,
                "course_id": None,
                "changed_fields": ["visibility"],
            },
        )
        self.assertNotIn("shared", str(audit.details))
        self.assertNotIn("不应出现在审计中的题库名称", str(audit.details))
        self.assertNotIn("不应出现在审计中的题库说明", str(audit.details))
