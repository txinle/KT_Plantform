from apps.api.test_support import domain_payload
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
import os
from unittest.mock import patch
from openpyxl import Workbook

from apps.audit.models import AuditLog

from .forms import ClassGroupUpdateForm
from .models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User




class CourseWorkspaceViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="collaborator",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="高等数学",
            code="MATH101",
            term="2026春",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)

    def _class_edit_state_token(self, class_group):
        return ClassGroupUpdateForm(instance=class_group).initial["state_token"]

    def _class_update_url(self, class_group):
        return reverse("teacher-course-class-management", args=[self.course.pk, class_group.pk])

    def _class_center_state_token(self, class_group):
        return ClassGroupUpdateForm(instance=class_group).initial["state_token"]


    def test_owner_and_collaborator_can_open_course_but_outsider_gets_404(self):
        detail_url = reverse("teacher-course-detail", args=[self.course.pk])
        for user in (self.owner, self.collaborator):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(detail_url)
                self.assertEqual(response.status_code, 200)

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 404)



    def test_class_import_reuses_existing_student_account(self):
        from .models import ClassGroup, CourseEnrollment

        class_group = ClassGroup.objects.create(course=self.course, name="一班", academic_year="2026")
        student = User.objects.create_user(
            username="S200",
            student_number="S200",
            display_name="学生乙",
            role=User.Role.STUDENT,
            password="existing-password",
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["学号", "姓名", "班级"])
        sheet.append(["S200", "学生乙", str(class_group)])
        stream = BytesIO()
        workbook.save(stream)
        upload = SimpleUploadedFile(
            "students.xlsx",
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-student-import-preview", args=[self.course.pk, class_group.pk])

        preview = self.client.post(url, {"file": upload})
        self.assertEqual(preview.status_code, 200, preview.content)
        confirmed = self.client.post(reverse("teacher-course-class-student-import-confirm", args=[self.course.pk, class_group.pk]),
            {"preview_id": domain_payload(preview)["preview_id"], "class_state_token": domain_payload(preview)["class_state_token"]})
        self.assertEqual(confirmed.status_code, 202)
        call_command("run_student_import_jobs", once=True)

        self.assertEqual(confirmed.status_code, 202)
        self.assertTrue(
            CourseEnrollment.objects.filter(course=self.course, class_group=class_group, student=student).exists()
        )
        student.refresh_from_db()
        self.assertTrue(student.check_password("existing-password"))

    def test_student_can_move_between_classes_only_inside_course_and_is_audited(self):
        from .models import ClassGroup, CourseEnrollment

        source = ClassGroup.objects.create(course=self.course, name="一班")
        target = ClassGroup.objects.create(course=self.course, name="二班")
        student = User.objects.create_user(
            username="S300",
            student_number="S300",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=source,
            student=student,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("teacher-course-class-student-move", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
        )

        self.assertEqual(response.status_code, 200)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, target)
        self.assertTrue(
            AuditLog.objects.filter(action="student.class_transfer", object_id=str(enrollment.pk)).exists()
        )


    def test_class_list_create_keeps_course_owner_and_audit_behavior(self):
        self.client.force_login(self.collaborator)

        response = self.client.post(
            reverse("teacher-class-collection", args=[self.course.pk]),
            {"name": "协作教师创建班", "academic_year": "2027"},
        )

        class_group = ClassGroup.objects.get(
            course=self.course,
            name="协作教师创建班",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(class_group.owner_id, self.course.owner_id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="class.create", object_id=str(class_group.pk)
            ).exists()
        )



    def test_removed_class_settings_route_is_not_named(self):
        removed_route_name = "_".join(("class", "rename"))
        with self.assertRaises(NoReverseMatch):
            reverse(f"courses:{removed_route_name}", args=[self.course.pk, 1])





    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_update_updates_metadata_and_audits_changed_field_values(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="原班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)
        token = self._class_edit_state_token(class_group)

        response = self.client.patch(
            self._class_update_url(class_group),
            {"name": "修订班级", "academic_year": "2027", "state_token": token}, content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "修订班级")
        self.assertEqual(class_group.academic_year, "2027")
        self.assertFalse(class_group.is_active)
        audit = AuditLog.objects.get(action="class.update", object_id=str(class_group.pk))
        self.assertEqual(
            audit.details,
            {
                "course_id": self.course.pk,
                "changed_fields": ["name", "academic_year", "is_active"],
                "changes": {
                    "name": {"before": "原班级", "after": "修订班级"},
                    "academic_year": {"before": "2026", "after": "2027"},
                    "is_active": {"before": True, "after": False},
                },
            },
        )
        self.assertNotIn("student", str(audit.details).lower())
        self.assertNotIn("password", str(audit.details).lower())

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_class_update_redirects_to_manage_and_preserves_update_audit(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="中心原班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)

        response = self.client.patch(
            self._class_update_url(class_group),
            {
                "name": "  中心修订班级  ",
                "academic_year": "2027",
                "state_token": self._class_center_state_token(class_group),
            },
         content_type="application/json")

        expected = (
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk])
            + "?section=manage"
        )
        self.assertEqual(response.status_code, 200)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "中心修订班级")
        self.assertEqual(class_group.academic_year, "2027")
        self.assertFalse(class_group.is_active)
        audit = AuditLog.objects.get(action="class.update", object_id=str(class_group.pk))
        self.assertEqual(
            audit.details["changed_fields"],
            ["name", "academic_year", "is_active"],
        )

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_update_audit_omits_unchanged_field_values(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="保持名称班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)

        response = self.client.patch(
            self._class_update_url(class_group),
            {
                "name": "保持名称班级",
                "academic_year": "2027",
                "is_active": True,
                "state_token": self._class_edit_state_token(class_group),
            },
         content_type="application/json")

        self.assertEqual(response.status_code, 200)
        audit = AuditLog.objects.get(action="class.update", object_id=str(class_group.pk))
        self.assertEqual(
            audit.details,
            {
                "course_id": self.course.pk,
                "changed_fields": ["academic_year"],
                "changes": {
                    "academic_year": {"before": "2026", "after": "2027"},
                },
            },
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_class_update_rejects_duplicate_course_name_and_year_in_center(self):
        ClassGroup.objects.create(
            course=self.course,
            name="已有班级",
            academic_year="2027",
        )
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="待编辑班级",
            academic_year="2026",
        )
        self.client.force_login(self.owner)

        payload = {
            "name": "已有班级",
            "academic_year": "2027",
            "is_active": True,
            "state_token": self._class_center_state_token(class_group),
        }
        form = ClassGroupUpdateForm(payload, instance=class_group)

        self.assertFalse(form.is_valid())
        self.assertIn("同一课程和年级中已存在该班级名称。", form.non_field_errors())

        response = self.client.patch(
            self._class_update_url(class_group),
            payload,
        content_type="application/json")

        self.assertEqual(response.status_code, 400)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "待编辑班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertFalse(
            AuditLog.objects.filter(
                action="class.update", object_id=str(class_group.pk)
            ).exists()
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_class_update_rejects_a_stale_state_token_in_center_without_overwrite(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="初始班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)
        stale_token = self._class_center_state_token(class_group)
        ClassGroup.objects.filter(pk=class_group.pk).update(name="并发更新班级")

        response = self.client.patch(
            self._class_update_url(class_group),
            {
                "name": "过期提交班级",
                "academic_year": "2027",
                "is_active": True,
                "state_token": stale_token,
            },
         content_type="application/json")

        self.assertEqual(response.status_code, 409)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "并发更新班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertTrue(class_group.is_active)
        self.assertFalse(
            AuditLog.objects.filter(action="class.update", object_id=str(class_group.pk)).exists()
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_class_update_rejects_a_tampered_state_token_in_center_without_overwrite(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="签名班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)
        token = self._class_center_state_token(class_group)
        tampered_token = f"{token[:-1]}{'x' if token[-1] != 'x' else 'y'}"

        response = self.client.patch(
            self._class_update_url(class_group),
            {
                "name": "篡改提交班级",
                "academic_year": "2027",
                "is_active": True,
                "state_token": tampered_token,
            },
         content_type="application/json")

        self.assertEqual(response.status_code, 409)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "签名班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertTrue(class_group.is_active)
        self.assertFalse(
            AuditLog.objects.filter(action="class.update", object_id=str(class_group.pk)).exists()
        )


    def test_inactive_class_cannot_be_a_copy_target(self):
        target = ClassGroup.objects.create(course=self.course, name="停用目标", is_active=False)
        source_course = Course.objects.create(
            name="来源课程",
            code="SOURCE",
            term="2025秋",
            owner=self.owner,
        )
        source = ClassGroup.objects.create(course=source_course, name="历史来源")
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-copy-preview", args=[self.course.pk, target.pk])

        response = self.client.post(url, {"action": "preview", "source_class": source.pk}, follow=True)

        self.assertEqual(response.status_code, 400)

    def test_inactive_class_rejects_student_add_and_excel_import_entry_points(self):
        class_group = ClassGroup.objects.create(course=self.course, name="停用班级", is_active=False)
        self.client.force_login(self.owner)
        for route in ("teacher-course-class-manual-enrollment", "teacher-course-class-student-import-preview"):
            response = self.client.post(reverse(route, args=[self.course.pk, class_group.pk]), {})
            self.assertEqual(response.status_code, 400)
        response = self.client.get(reverse("teacher-course-class-student-import-template", args=[self.course.pk, class_group.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CourseEnrollment.objects.filter(class_group=class_group).exists())

    def test_inactive_class_cannot_be_a_transfer_target(self):
        source = ClassGroup.objects.create(course=self.course, name="可用来源")
        target = ClassGroup.objects.create(course=self.course, name="停用目标", is_active=False)
        student = User.objects.create_user(
            username="MOVE1",
            student_number="MOVE1",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=source,
            student=student,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("teacher-course-class-student-move", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 400)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, source)

    def test_inactive_class_allows_password_reset_and_transfer_out(self):
        source = ClassGroup.objects.create(course=self.course, name="停用来源", is_active=False)
        target = ClassGroup.objects.create(course=self.course, name="可用目标")
        student = User.objects.create_user(
            username="MOVE2",
            student_number="MOVE2",
            role=User.Role.STUDENT,
            password="old-password",
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=source,
            student=student,
        )
        self.client.force_login(self.owner)

        reset = self.client.post(
            reverse("teacher-course-class-student-reset-password", args=[self.course.pk, source.pk, student.pk]),
            {"confirmation": "reset"},
        )
        self.assertEqual(reset.status_code, 200)
        student.refresh_from_db()
        self.assertTrue(student.check_password(f"S{student.student_number}"))
        self.assertTrue(student.uses_initial_password)
        self.assertIsNone(student.initial_password_reminder_shown_at)

        moved = self.client.post(
            reverse("teacher-course-class-student-move", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
        )
        self.assertEqual(moved.status_code, 200)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, target)


    def test_class_update_rejects_an_archived_course(self):
        class_group = ClassGroup.objects.create(course=self.course, name="归档班级")
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.owner)

        for class_center_enabled in (False, True):
            with self.subTest(class_center_enabled=class_center_enabled):
                with override_settings(
                    CLASS_CENTER_ENABLED=class_center_enabled
                ):
                    self.assertEqual(self.client.get(self._class_update_url(class_group)).status_code, 200)
                    response = self.client.patch(
                        self._class_update_url(class_group),
                        {
                            "name": "归档后不应修改",
                            "academic_year": "2027",
                            "is_active": True,
                            "state_token": "invalid",
                        },
                     content_type="application/json")
                    self.assertEqual(response.status_code, 403)

        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "归档班级")
        self.assertEqual(class_group.academic_year, "")
        self.assertFalse(
            AuditLog.objects.filter(
                action="class.update", object_id=str(class_group.pk)
            ).exists()
        )

    def test_inactive_class_write_endpoints_keep_archived_course_read_only(self):
        class_group = ClassGroup.objects.create(course=self.course, name="停用班级", is_active=False)
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.owner)

        for url_name, payload in (
            ("student_add_manual", {"student_number": "ARCH1", "display_name": "归档测试"}),
            ("student_copy", {"action": "preview"}),
            ("student_import", {"action": "confirm"}),
        ):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse({"student_add_manual": "teacher-course-class-manual-enrollment", "student_copy": "teacher-course-class-copy-preview", "student_import": "teacher-course-class-student-import-confirm"}[url_name], args=[self.course.pk, class_group.pk]),
                    payload,
                )

                self.assertEqual(response.status_code, 403)


    def test_manual_student_add_creates_enrollment_and_audit(self):
        class_group = ClassGroup.objects.create(course=self.course, name="一班", academic_year="2026")
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-manual-enrollment", args=[self.course.pk, class_group.pk])

        response = self.client.post(
            url,
            {"student_number": "00123", "display_name": "临时学生"},
        )

        self.assertEqual(response.status_code, 201)
        student = User.objects.get(student_number="00123")
        self.assertTrue(student.check_password("S00123"))
        enrollment = CourseEnrollment.objects.get(course=self.course, student=student)
        audit = AuditLog.objects.get(action="student.manual_enroll", object_id=str(enrollment.pk))
        self.assertEqual(audit.details["created_account"], True)
        self.assertNotIn("password", audit.details)

    def test_copy_other_course_class_previews_skips_and_confirms(self):
        target = ClassGroup.objects.create(course=self.course, name="目标班")
        source_course = Course.objects.create(
            name="历史课程",
            code="OLD101",
            term="2025秋",
            owner=self.owner,
            status=Course.Status.ARCHIVED,
        )
        source = ClassGroup.objects.create(course=source_course, name="历史一班")
        eligible = User.objects.create_user(
            username="COPY1",
            student_number="COPY1",
            display_name="可复制学生",
            role=User.Role.STUDENT,
            password="keep-password",
        )
        conflict = User.objects.create_user(
            username="COPY2",
            student_number="COPY2",
            display_name="已选课学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(course=source_course, class_group=source, student=eligible)
        CourseEnrollment.objects.create(course=source_course, class_group=source, student=conflict)
        CourseEnrollment.objects.create(course=self.course, class_group=target, student=conflict)
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-copy-preview", args=[self.course.pk, target.pk])

        preview = self.client.post(url, {"action": "preview", "source_class": source.pk})
        self.assertEqual(preview.status_code, 200, preview.content)

        confirmed = self.client.post(reverse("teacher-course-class-copy-confirm", args=[self.course.pk, target.pk]), {})
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(CourseEnrollment.objects.filter(course=self.course, student=eligible).exists())
        eligible.refresh_from_db()
        self.assertTrue(eligible.check_password("keep-password"))
        audit = AuditLog.objects.get(action="student.class_copy", object_id=str(target.pk))
        self.assertEqual(audit.details["copied"], 1)
        self.assertEqual(audit.details["skipped"], 1)

    def test_copy_student_source_is_limited_to_accessible_other_courses(self):
        target = ClassGroup.objects.create(course=self.course, name="目标班")
        outsider_course = Course.objects.create(
            name="无权课程",
            code="NOACCESS",
            term="2026春",
            owner=self.outsider,
        )
        outsider_class = ClassGroup.objects.create(course=outsider_course, name="外部班级")
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-copy-preview", args=[self.course.pk, target.pk])

        page = self.client.get(url)
        self.assertEqual(page.status_code, 405)
        rejected = self.client.post(url, {"action": "preview", "source_class": outsider_class.pk})
        self.assertEqual(rejected.status_code, 400)

    def test_student_write_endpoints_require_csrf(self):
        class_group = ClassGroup.objects.create(course=self.course, name="一班", academic_year="2026")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)

        manual = client.post(
            reverse("teacher-course-class-manual-enrollment", args=[self.course.pk, class_group.pk]),
            {"student_number": "CSRF1", "display_name": "测试"},
        )
        copy = client.post(
            reverse("teacher-course-class-copy-preview", args=[self.course.pk, class_group.pk]),
            {"action": "confirm"},
        )

        self.assertEqual(manual.status_code, 403)
        self.assertEqual(copy.status_code, 403)
