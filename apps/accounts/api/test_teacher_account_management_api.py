import json
from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
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

    @staticmethod
    def csv_upload(rows, *, encoding="utf-8-sig"):
        content = "学号,姓名,班级\r\n" + "".join(
            f"{number},{name},{class_name}\r\n"
            for number, name, class_name in rows
        )
        return SimpleUploadedFile(
            "学生名单.csv", content.encode(encoding), content_type="text/csv"
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
        self.assertTrue(preview.json()["preview_id"])
        self.assertTrue(preview.json()["class_state_token"])
        self.assertFalse(
            CourseEnrollment.objects.filter(
                course=self.course, student=existing
            ).exists()
        )

        confirmed = self.post_json(
            confirm_url,
            {
                "preview_id": preview.json()["preview_id"],
                "class_state_token": preview.json()["class_state_token"],
            },
        )

        self.assertEqual(confirmed.status_code, 202)
        self.assertEqual(confirmed.json()["status"], "queued")
        self.assertEqual(confirmed.json()["total_rows"], 2)
        self.assertEqual(confirmed.json()["created_accounts"], 0)
        self.assertEqual(confirmed.json()["enrollments"], 0)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password("preserved-password"))
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course).exists())

        repeated = self.post_json(
            confirm_url,
            {
                "preview_id": preview.json()["preview_id"],
                "class_state_token": preview.json()["class_state_token"],
            },
        )
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.json()["job_id"], confirmed.json()["job_id"])

        status_response = self.client.get(
            f"{self.manage_base}students/import/jobs/{confirmed.json()['job_id']}/"
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "queued")
        self.assertEqual(status_response.json()["total_rows"], 2)

    def test_course_csv_preview_marks_transfer_and_same_class_skip(self):
        moving = User.objects.create_user(
            username="MOVE-001", student_number="MOVE-001",
            display_name="转班学生", role=User.Role.STUDENT,
        )
        already_here = User.objects.create_user(
            username="HERE-001", student_number="HERE-001",
            display_name="本班学生", role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=self.other_class, student=moving
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=self.class_group, student=already_here
        )

        preview = self.client.post(
            f"{self.manage_base}students/import/preview/",
            {"file": self.csv_upload([
                ["MOVE-001", "转班学生", str(self.class_group)],
                ["HERE-001", "本班学生", str(self.class_group)],
                ["NEW-001", "新学生", str(self.class_group)],
            ])},
        )

        self.assertEqual(preview.status_code, 200)
        rows = preview.json()["preview"]
        self.assertEqual(
            [(row["account_action"], row["enrollment_action"]) for row in rows],
            [("reuse", "transfer"), ("reuse", "skip"), ("create", "create")],
        )
        self.assertEqual(rows[0]["source_class_name"], str(self.other_class))
        self.assertEqual(
            self.client.session[
                f"course_student_import:{self.course.pk}:{self.class_group.pk}"
            ]["rows"][0]["source_class_id"],
            self.other_class.pk,
        )

        confirmed = self.post_json(
            f"{self.manage_base}students/import/confirm/",
            {
                "preview_id": preview.json()["preview_id"],
                "class_state_token": preview.json()["class_state_token"],
            },
        )
        self.assertEqual(confirmed.status_code, 202)
        call_command("run_student_import_jobs", once=True)
        status_response = self.client.get(
            f"{self.manage_base}students/import/jobs/{confirmed.json()['job_id']}/"
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "succeeded")
        self.assertEqual(
            (status_response.json()["enrollments"],
             status_response.json()["transferred"],
             status_response.json()["skipped"]),
            (1, 1, 1),
        )
        self.assertEqual(
            CourseEnrollment.objects.get(course=self.course, student=moving).class_group_id,
            self.class_group.pk,
        )

    def test_course_import_accepts_excel_saved_chinese_csv(self):
        preview = self.client.post(
            f"{self.manage_base}students/import/preview/",
            {"file": self.csv_upload(
                [["GB-001", "中文学生", str(self.class_group)]],
                encoding="gb18030",
            )},
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["preview"][0]["display_name"], "中文学生")

    def test_course_import_rejects_preview_after_class_changes(self):
        preview_url = f"{self.manage_base}students/import/preview/"
        confirm_url = f"{self.manage_base}students/import/confirm/"
        preview = self.client.post(
            preview_url,
            {
                "file": self.workbook_upload(
                    [["STALE-IMPORT-001", "状态过期学生", str(self.class_group)]]
                )
            },
        )
        self.class_group.name = "已修改班级"
        self.class_group.save(update_fields=["name"])

        confirmed = self.post_json(
            confirm_url,
            {
                "preview_id": preview.json()["preview_id"],
                "class_state_token": preview.json()["class_state_token"],
            },
        )

        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(confirmed.json()["error"]["code"], "IMPORT_PREVIEW_STALE")
        self.assertIn("重新上传", confirmed.json()["error"]["message"])
        self.assertFalse(User.objects.filter(student_number="STALE-IMPORT-001").exists())

    def test_course_import_confirmation_only_queues_174_students(self):
        rows = [
            [f"BATCH-{index:03d}", f"批量学生{index:03d}", str(self.class_group)]
            for index in range(174)
        ]
        preview = self.client.post(
            f"{self.manage_base}students/import/preview/",
            {"file": self.workbook_upload(rows)},
        )

        with patch(
            "apps.accounts.student_import_jobs.make_password",
            side_effect=AssertionError("confirmation must not hash passwords"),
        ):
            confirmed = self.post_json(
                f"{self.manage_base}students/import/confirm/",
                {
                    "preview_id": preview.json()["preview_id"],
                    "class_state_token": preview.json()["class_state_token"],
                },
            )

        self.assertEqual(confirmed.status_code, 202)
        self.assertEqual(confirmed.json()["total_rows"], 174)
        self.assertFalse(User.objects.filter(student_number__startswith="BATCH-").exists())
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course).exists())

    def test_course_class_update_returns_specific_stale_state_error(self):
        initial = self.client.get(f"{self.manage_base}manage/").json()
        stale_token = initial["class_group"]["state_token"]
        self.class_group.name = "服务端新名称"
        self.class_group.save(update_fields=["name"])

        response = self.patch_json(
            f"{self.manage_base}manage/",
            {
                "name": "页面旧名称",
                "academic_year": self.class_group.academic_year,
                "is_active": True,
                "state_token": stale_token,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "CLASS_STATE_STALE")
        self.assertEqual(
            response.json()["error"]["message"],
            "班级信息已被其他操作更新，请刷新后重试。",
        )

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

        expired = self.post_json(
            confirm_url,
            {
                "preview_id": valid_preview.json()["preview_id"],
                "class_state_token": valid_preview.json()["class_state_token"],
            },
        )
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
            f"{self.manage_base}students/import/confirm/",
            {
                "preview_id": course_preview.json()["preview_id"],
                "class_state_token": course_preview.json()["class_state_token"],
            },
        )
        self.assertEqual(course_confirmed.status_code, 202)
        self.assertEqual(course_confirmed.json()["status"], "queued")

    def test_course_class_delete_requires_confirmation_and_cleans_its_policy(self):
        delete_url = (
            f"{self.global_base}courses/{self.course.pk}/classes/{self.other_class.pk}/delete/"
        )
        policy_pk = self.other_class.student_analytics_policy.pk

        rejected = self.post_json(delete_url, {})

        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=self.other_class.pk).exists())
        self.assertTrue(ClassStudentAnalyticsPolicy.objects.filter(pk=policy_pk).exists())

        preview = self.client.get(delete_url).json()
        deleted = self.post_json(delete_url, {
            "confirmation": "delete", "confirmation_token": preview["confirmation_token"],
        })

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True, "cleanup_pending": False, "cleanup_id": None})
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
            CourseEnrollment.objects.filter(
                course=self.course,
                student_id=student_id,
                is_active=True,
            ).exists()
        )
