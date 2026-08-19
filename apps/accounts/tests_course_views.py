from django.test import Client, TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
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

    def test_teacher_home_and_course_list_are_course_first(self):
        self.client.force_login(self.owner)

        home = self.client.get(reverse("home"))
        self.assertRedirects(home, reverse("courses:list"))
        response = self.client.get(reverse("courses:list"))
        self.assertContains(response, "我的课程")
        self.assertContains(response, "高等数学")
        self.assertContains(response, "MATH101")
        self.assertNotContains(response, "KC 库")

    def test_owner_and_collaborator_can_open_course_but_outsider_gets_404(self):
        detail_url = reverse("courses:detail", args=[self.course.pk])
        for user in (self.owner, self.collaborator):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(detail_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "题库")
                self.assertContains(response, "班级")
                self.assertContains(response, "实验")

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

    def test_course_creation_and_archive_are_audited(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("courses:create"),
            {
                "name": "大学物理",
                "code": "PHYS101",
                "term": "2026春",
                "description": "研究课程",
            },
        )
        created = Course.objects.get(code="PHYS101")
        self.assertRedirects(response, reverse("courses:detail", args=[created.pk]))
        self.assertTrue(AuditLog.objects.filter(action="course.create", object_id=str(created.pk)).exists())

        archive = self.client.post(reverse("courses:archive", args=[created.pk]))
        self.assertRedirects(archive, reverse("courses:detail", args=[created.pk]))
        created.refresh_from_db()
        self.assertEqual(created.status, Course.Status.ARCHIVED)
        self.assertTrue(AuditLog.objects.filter(action="course.archive", object_id=str(created.pk)).exists())

    def test_only_owner_can_manage_course_settings_and_collaborators(self):
        settings_url = reverse("courses:settings", args=[self.course.pk])
        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.get(settings_url).status_code, 403)

        self.client.force_login(self.owner)
        response = self.client.post(
            settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "更新说明",
                "teachers": [self.outsider.pk],
            },
        )
        self.assertRedirects(response, settings_url)
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "更新说明")
        self.assertQuerySetEqual(
            self.course.teacher_links.values_list("teacher", flat=True),
            [self.outsider.pk],
        )

    def test_class_import_reuses_existing_student_account(self):
        from .models import ClassGroup, CourseEnrollment

        class_group = ClassGroup.objects.create(course=self.course, name="一班")
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
        url = reverse("courses:student_import", args=[self.course.pk, class_group.pk])

        preview = self.client.post(url, {"file": upload})
        self.assertContains(preview, "S200")
        confirmed = self.client.post(url, {"action": "confirm"})

        self.assertRedirects(
            confirmed,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
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
            reverse("courses:move_student", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
        )

        self.assertRedirects(response, reverse("courses:class_detail", args=[self.course.pk, target.pk]))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, target)
        self.assertTrue(
            AuditLog.objects.filter(action="student.class_transfer", object_id=str(enrollment.pk)).exists()
        )

    def test_class_list_searches_name_or_academic_year_and_preserves_query(self):
        ClassGroup.objects.create(course=self.course, name="认知追踪实验班", academic_year="2026")
        ClassGroup.objects.create(course=self.course, name="数据分析班", academic_year="2025")
        self.client.force_login(self.owner)
        url = reverse("courses:classes", args=[self.course.pk])

        by_name = self.client.get(url, {"q": "  认知  "})
        self.assertContains(by_name, "认知追踪实验班")
        self.assertNotContains(by_name, "数据分析班")
        self.assertContains(by_name, 'value="认知"')
        self.assertContains(by_name, "1 个")

        by_year = self.client.get(url, {"q": "2025"})
        self.assertContains(by_year, "数据分析班")
        self.assertNotContains(by_year, "认知追踪实验班")
        self.assertContains(by_year, "清除筛选")

    def test_class_name_can_be_trimmed_renamed_and_audited(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="错误班名",
            academic_year="2026",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("courses:class_rename", args=[self.course.pk, class_group.pk]),
            {"name": "  修正班名  ", "q": "错误"},
        )

        self.assertRedirects(response, f'{reverse("courses:classes", args=[self.course.pk])}?q=%E9%94%99%E8%AF%AF')
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "修正班名")
        audit = AuditLog.objects.get(action="class.rename", object_id=str(class_group.pk))
        self.assertEqual(
            audit.details,
            {"course_id": self.course.pk, "changed_fields": ["name"]},
        )

    def test_class_edit_updates_metadata_and_audits_changed_fields(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="原班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk]),
            {"name": "修订班级", "academic_year": "2027"},
        )

        self.assertRedirects(
            response,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
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
            },
        )

    def test_class_edit_rejects_duplicate_course_name_and_year_in_chinese(self):
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

        payload = {"name": "已有班级", "academic_year": "2027", "is_active": "on"}
        form = ClassGroupUpdateForm(payload, instance=class_group)

        self.assertFalse(form.is_valid())
        self.assertIn("同一课程和学年中已存在该班级名称。", form.non_field_errors())

        response = self.client.post(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk]),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "同一课程和学年中已存在该班级名称。")
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "待编辑班级")
        self.assertEqual(class_group.academic_year, "2026")

    def test_class_rename_rejects_duplicate_name_and_archived_course(self):
        source = ClassGroup.objects.create(course=self.course, name="一班", academic_year="2026")
        target = ClassGroup.objects.create(course=self.course, name="二班", academic_year="2026")
        self.client.force_login(self.owner)
        url = reverse("courses:class_rename", args=[self.course.pk, target.pk])

        duplicate = self.client.post(url, {"name": source.name})
        self.assertRedirects(duplicate, reverse("courses:classes", args=[self.course.pk]))
        target.refresh_from_db()
        self.assertEqual(target.name, "二班")
        self.assertFalse(AuditLog.objects.filter(action="class.rename", object_id=str(target.pk)).exists())

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.assertEqual(self.client.post(url, {"name": "归档后改名"}).status_code, 403)

    def test_inactive_class_detail_explains_write_restrictions_and_hides_import(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="历史班级",
            is_active=False,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk])
        )

        self.assertContains(response, "班级已停用。")
        self.assertContains(response, "不能向此班级手动新增、Excel 导入、复制或转入学生")
        self.assertNotContains(
            response,
            reverse("courses:student_add", args=[self.course.pk, class_group.pk]),
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
        url = reverse("courses:student_copy", args=[self.course.pk, target.pk])

        response = self.client.post(url, {"action": "preview", "source_class": source.pk}, follow=True)

        self.assertRedirects(
            response,
            reverse("courses:class_detail", args=[self.course.pk, target.pk]),
        )
        self.assertContains(response, "目标班级已停用，不能复制学生。")

    def test_inactive_class_rejects_student_add_and_excel_import_entry_points(self):
        class_group = ClassGroup.objects.create(course=self.course, name="停用班级", is_active=False)
        self.client.force_login(self.owner)
        detail_url = reverse("courses:class_detail", args=[self.course.pk, class_group.pk])

        for url_name in ("student_add", "student_import", "student_import_template"):
            with self.subTest(url_name=url_name):
                response = self.client.get(
                    reverse(f"courses:{url_name}", args=[self.course.pk, class_group.pk]),
                    follow=True,
                )

                self.assertRedirects(response, detail_url)
                self.assertContains(response, "目标班级已停用，不能新增学生。")

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
            reverse("courses:move_student", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
            follow=True,
        )

        self.assertRedirects(response, reverse("courses:class_detail", args=[self.course.pk, source.pk]))
        self.assertContains(response, "目标班级已停用，不能转入学生。")
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
            reverse("courses:reset_password", args=[self.course.pk, source.pk, student.pk]),
            {"password": "new-password"},
        )
        self.assertRedirects(reset, reverse("courses:class_detail", args=[self.course.pk, source.pk]))
        student.refresh_from_db()
        self.assertTrue(student.check_password("new-password"))

        moved = self.client.post(
            reverse("courses:move_student", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
        )
        self.assertRedirects(moved, reverse("courses:class_detail", args=[self.course.pk, target.pk]))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, target)

    def test_class_edit_enforces_course_scope_and_csrf(self):
        class_group = ClassGroup.objects.create(course=self.course, name="受限班级")
        url = reverse("courses:class_edit", args=[self.course.pk, class_group.pk])

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.get(url).status_code, 200)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(
            csrf_client.post(
                url,
                {"name": "无令牌", "academic_year": "2026", "is_active": "on"},
            ).status_code,
            403,
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
                    reverse(f"courses:{url_name}", args=[self.course.pk, class_group.pk]),
                    payload,
                )

                self.assertEqual(response.status_code, 403)

    def test_student_add_hub_exposes_all_three_supported_methods(self):
        class_group = ClassGroup.objects.create(course=self.course, name="一班")
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("courses:student_add", args=[self.course.pk, class_group.pk])
        )

        self.assertContains(response, "手动添加单个学生")
        self.assertContains(response, "Excel 批量导入")
        self.assertContains(response, "从其他课程班级复制")
        self.assertContains(
            response,
            reverse("courses:student_import", args=[self.course.pk, class_group.pk]),
        )
        self.assertContains(
            response,
            reverse("courses:student_add_manual", args=[self.course.pk, class_group.pk]),
        )
        self.assertContains(
            response,
            reverse("courses:student_copy", args=[self.course.pk, class_group.pk]),
        )

    def test_manual_student_add_creates_enrollment_and_audit(self):
        class_group = ClassGroup.objects.create(course=self.course, name="一班")
        self.client.force_login(self.owner)
        url = reverse("courses:student_add_manual", args=[self.course.pk, class_group.pk])

        response = self.client.post(
            url,
            {"student_number": "00123", "display_name": "临时学生"},
        )

        self.assertRedirects(response, reverse("courses:class_detail", args=[self.course.pk, class_group.pk]))
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
        url = reverse("courses:student_copy", args=[self.course.pk, target.pk])

        preview = self.client.post(url, {"action": "preview", "source_class": source.pk})
        self.assertContains(preview, "可复制学生")
        self.assertContains(preview, "已选课学生")
        self.assertContains(preview, "将跳过")

        confirmed = self.client.post(url, {"action": "confirm"})
        self.assertRedirects(confirmed, reverse("courses:class_detail", args=[self.course.pk, target.pk]))
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
        url = reverse("courses:student_copy", args=[self.course.pk, target.pk])

        page = self.client.get(url)
        self.assertNotContains(page, "外部班级")
        rejected = self.client.post(url, {"action": "preview", "source_class": outsider_class.pk})
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "请选择一个有效的选项")

    def test_student_write_endpoints_require_csrf(self):
        class_group = ClassGroup.objects.create(course=self.course, name="一班")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)

        manual = client.post(
            reverse("courses:student_add_manual", args=[self.course.pk, class_group.pk]),
            {"student_number": "CSRF1", "display_name": "测试"},
        )
        copy = client.post(
            reverse("courses:student_copy", args=[self.course.pk, class_group.pk]),
            {"action": "confirm"},
        )

        self.assertEqual(manual.status_code, 403)
        self.assertEqual(copy.status_code, 403)
