import json
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook

from apps.accounts.models import (
    ClassGroup,
    ClassStudentAnalyticsPolicy,
    Course,
    CourseEnrollment,
    User,
)
from apps.audit.models import AuditLog


class TeacherAccountManagementApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="account-management-owner",
            role=User.Role.TEACHER,
            display_name="负责人老师",
        )
        self.outsider = User.objects.create_user(
            username="account-management-outsider",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="account-management-student",
            role=User.Role.STUDENT,
        )
        self.legacy_class = ClassGroup.objects.create(
            owner=self.owner,
            name="遗留账户班",
            academic_year="2026",
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="课程班级管理 API",
            code="ACCOUNT-API",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="目标班",
            academic_year="2026",
        )
        self.other_class = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="可转入班",
            academic_year="2026",
        )
        self.global_base = "/api/v1/teacher/accounts/"
        self.manage_base = (
            f"{self.global_base}courses/{self.course.pk}/classes/{self.class_group.pk}/"
        )
        self.client.force_login(self.owner)

    def post_json(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def patch_json(self, url, payload):
        return self.client.patch(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    @staticmethod
    def workbook_upload(rows, *, name="学生名单.xlsx"):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["学号", "姓名", "班级"])
        for row in rows:
            worksheet.append(row)
        stream = BytesIO()
        workbook.save(stream)
        return SimpleUploadedFile(
            name,
            stream.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

    def test_global_account_forms_preserve_existing_form_validation(self):
        invalid = self.post_json(
            f"{self.global_base}students/",
            {
                "student_number": "",
                "display_name": "",
                "class_group": self.legacy_class.pk,
                "password": "short",
            },
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertIn("student_number", invalid.json()["error"]["fields"])

        created = self.post_json(
            f"{self.global_base}students/",
            {
                "student_number": "LEGACY-001",
                "display_name": "账户学生",
                "class_group": self.legacy_class.pk,
                "password": "account-password",
            },
        )

        self.assertEqual(created.status_code, 201)
        account = User.objects.get(student_number="LEGACY-001")
        self.assertEqual(account.class_group_id, self.legacy_class.pk)
        self.assertTrue(account.check_password("SLEGACY-001"))
        self.assertTrue(account.uses_initial_password)
        self.assertIsNone(account.initial_password_reminder_shown_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="student.create", object_id=str(account.pk)
            ).exists()
        )

    def test_course_management_access_is_server_authorized(self):
        self.client.force_login(self.student)
        forbidden = self.client.get(f"{self.manage_base}manage/")
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.outsider)
        hidden = self.client.get(f"{self.manage_base}manage/")
        self.assertEqual(hidden.status_code, 404)

    def test_course_import_preview_confirm_reuses_existing_account_without_password_change(self):
        existing = User.objects.create_user(
            username="REUSE-001",
            student_number="REUSE-001",
            display_name="已有学生",
            role=User.Role.STUDENT,
            password="preserved-password",
        )
        preview_url = f"{self.manage_base}students/import/preview/"
        confirm_url = f"{self.manage_base}students/import/confirm/"

        preview = self.client.post(
            preview_url,
            {
                "file": self.workbook_upload(
                    [
                        ["REUSE-001", "已有学生", str(self.class_group)],
                        ["NEW-002", "新建学生", str(self.class_group)],
                    ]
                )
            },
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["preview"][0]["account_action"], "reuse")
        self.assertFalse(
            CourseEnrollment.objects.filter(
                course=self.course, student=existing
            ).exists()
        )

        confirmed = self.post_json(confirm_url, {})

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["created_accounts"], 1)
        self.assertEqual(confirmed.json()["enrollments"], 2)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password("preserved-password"))
        self.assertTrue(
            CourseEnrollment.objects.filter(
                course=self.course, class_group=self.class_group, student=existing
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="student.batch_import", object_id=str(self.class_group.pk)
            ).exists()
        )

        expired = self.post_json(confirm_url, {})
        self.assertEqual(expired.status_code, 400)
        self.assertIn("预览已失效", expired.json()["error"]["message"])

    def test_course_invalid_repreview_clears_the_previous_import_preview(self):
        preview_url = f"{self.manage_base}students/import/preview/"
        confirm_url = f"{self.manage_base}students/import/confirm/"
        valid_preview = self.client.post(
            preview_url,
            {
                "file": self.workbook_upload(
                    [["STALE-001", "旧预览学生", str(self.class_group)]]
                )
            },
        )
        self.assertEqual(valid_preview.status_code, 200)

        invalid_preview = self.client.post(
            preview_url,
            {"file": SimpleUploadedFile("invalid.csv", b"student,name,class")},
        )
        self.assertEqual(invalid_preview.status_code, 400)

        expired = self.post_json(confirm_url, {})
        self.assertEqual(expired.status_code, 400)
        self.assertIn("预览已失效", expired.json()["error"]["message"])

    def test_all_confirm_endpoints_accept_existing_empty_request_bodies(self):
        legacy_preview = self.client.post(
            f"{self.global_base}students/import/preview/",
            {
                "file": self.workbook_upload(
                    [["LEGACY-EMPTY-001", "遗留空载荷学生", str(self.legacy_class)]]
                )
            },
        )
        self.assertEqual(legacy_preview.status_code, 200)
        legacy_confirmed = self.post_json(
            f"{self.global_base}students/import/confirm/", {}
        )
        self.assertEqual(legacy_confirmed.status_code, 200)
        self.assertEqual(legacy_confirmed.json(), {"imported": 1})

        source_course = Course.objects.create(
            owner=self.owner,
            name="复制来源课程",
            code="COPY-SOURCE",
            term="2026 秋",
        )
        source_class = ClassGroup.objects.create(
            course=source_course,
            owner=self.owner,
            name="复制来源班",
            academic_year="2026",
        )
        source_student = User.objects.create_user(
            username="COPY-EMPTY-001",
            student_number="COPY-EMPTY-001",
            display_name="复制空载荷学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=source_course,
            class_group=source_class,
            student=source_student,
        )
        copy_preview = self.post_json(
            f"{self.manage_base}students/copy/preview/",
            {"source_class": source_class.pk},
        )
        self.assertEqual(copy_preview.status_code, 200)
        copy_confirmed = self.post_json(
            f"{self.manage_base}students/copy/confirm/", {}
        )
        self.assertEqual(copy_confirmed.status_code, 200)
        self.assertEqual(copy_confirmed.json(), {"copied": 1, "skipped": 0})

        course_preview = self.client.post(
            f"{self.manage_base}students/import/preview/",
            {
                "file": self.workbook_upload(
                    [["COURSE-EMPTY-001", "课程空载荷学生", str(self.class_group)]]
                )
            },
        )
        self.assertEqual(course_preview.status_code, 200)
        course_confirmed = self.post_json(
            f"{self.manage_base}students/import/confirm/", {}
        )
        self.assertEqual(course_confirmed.status_code, 200)
        self.assertEqual(
            course_confirmed.json(), {"created_accounts": 1, "enrollments": 1}
        )

    def test_course_class_delete_requires_confirmation_and_cleans_its_policy(self):
        delete_url = (
            f"{self.global_base}courses/{self.course.pk}/classes/{self.other_class.pk}/delete/"
        )
        policy_pk = self.other_class.student_analytics_policy.pk

        rejected = self.post_json(delete_url, {})

        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=self.other_class.pk).exists())
        self.assertTrue(ClassStudentAnalyticsPolicy.objects.filter(pk=policy_pk).exists())

        deleted = self.post_json(delete_url, {"confirmation": "delete"})

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})
        self.assertFalse(ClassGroup.objects.filter(pk=self.other_class.pk).exists())
        self.assertFalse(ClassStudentAnalyticsPolicy.objects.filter(pk=policy_pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="class.delete").exists())

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_course_management_mutations_use_existing_services_and_policy_rules(self):
        initial = self.client.get(f"{self.manage_base}manage/")
        self.assertEqual(initial.status_code, 200)
        state_token = initial.json()["class_group"]["state_token"]

        updated = self.patch_json(
            f"{self.manage_base}manage/",
            {
                "name": "已更新目标班",
                "academic_year": "2027",
                "is_active": True,
                "state_token": state_token,
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.class_group.refresh_from_db()
        self.assertEqual(self.class_group.name, "已更新目标班")

        added = self.post_json(
            f"{self.manage_base}students/manual/",
            {"student_number": "MOVE-001", "display_name": "待移动学生"},
        )
        self.assertEqual(added.status_code, 201)
        student_id = added.json()["student"]["id"]

        moved = self.post_json(
            f"{self.manage_base}students/{student_id}/move/",
            {"class_group": self.other_class.pk},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["class_group"]["id"], self.other_class.pk)

        reset = self.post_json(
            f"{self.global_base}courses/{self.course.pk}/classes/{self.other_class.pk}/students/{student_id}/reset-password/",
            {"confirmation": "reset"},
        )
        self.assertEqual(reset.status_code, 200)
        student = User.objects.get(pk=student_id)
        self.assertTrue(student.check_password("SMOVE-001"))
        self.assertTrue(student.uses_initial_password)
        self.assertIsNone(student.initial_password_reminder_shown_at)

        policy_url = f"{self.manage_base}student-visibility/"
        policy = self.patch_json(
            policy_url,
            {
                "show_scores": False,
                "show_correct_rate": False,
                "show_item_results": True,
                "show_class_average": True,
                "rank_mode": "self",
                "show_kc_rates": True,
                "show_trend": True,
            },
        )
        self.assertEqual(policy.status_code, 200)
        saved_policy = ClassStudentAnalyticsPolicy.objects.get(
            class_group=self.class_group
        )
        self.assertFalse(saved_policy.show_correct_rate)
        self.assertFalse(saved_policy.show_kc_rates)
        self.assertFalse(saved_policy.show_trend)
        self.assertEqual(saved_policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.NONE)

        removed = self.post_json(
            f"{self.global_base}courses/{self.course.pk}/classes/{self.other_class.pk}/students/{student_id}/remove/",
            {"confirmation": "delete"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(
            CourseEnrollment.objects.filter(course=self.course, student_id=student_id).exists()
        )
