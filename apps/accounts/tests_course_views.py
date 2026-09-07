from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from openpyxl import Workbook, load_workbook

from apps.audit.models import AuditLog

from .forms import ClassGroupForm, ClassGroupUpdateForm
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
        response = self.client.get(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.context["form"].initial["state_token"]

    def _class_update_url(self, class_group):
        return reverse("courses:class_update", args=[self.course.pk, class_group.pk])

    def _class_center_state_token(self, class_group):
        response = self.client.get(
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
            {"section": "manage"},
        )
        self.assertEqual(response.status_code, 200)
        return response.context["class_settings_form"].initial["state_token"]

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

    def test_course_collaborator_can_manage_course_settings_and_collaborators(self):
        settings_url = reverse("courses:settings", args=[self.course.pk])
        self.client.force_login(self.collaborator)
        settings_page = self.client.get(settings_url)
        self.assertEqual(settings_page.status_code, 200)
        state_token = settings_page.context["form"].initial["state_token"]
        collaborator_update = self.client.post(
            settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "协作教师更新",
                "teachers": [self.collaborator.pk],
                "state_token": state_token,
            },
        )
        self.assertRedirects(collaborator_update, settings_url)
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "协作教师更新")

        self.client.force_login(self.owner)
        state_token = self.client.get(settings_url).context["form"].initial["state_token"]
        response = self.client.post(
            settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "更新说明",
                "teachers": [self.outsider.pk],
                "state_token": state_token,
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

    def test_class_list_create_keeps_course_owner_and_audit_behavior(self):
        self.client.force_login(self.collaborator)

        response = self.client.post(
            reverse("courses:classes", args=[self.course.pk]),
            {"name": "协作教师创建班", "academic_year": "2027"},
        )

        class_group = ClassGroup.objects.get(
            course=self.course,
            name="协作教师创建班",
        )
        self.assertRedirects(
            response,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
        self.assertEqual(class_group.owner_id, self.course.owner_id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="class.create", object_id=str(class_group.pk)
            ).exists()
        )

    def test_class_grade_uses_four_digit_validation_and_visible_guidance(self):
        valid = ClassGroupForm({"name": "电子信息工程一班", "academic_year": "2025"})
        blank = ClassGroupForm({"name": "不设年级班", "academic_year": ""})
        invalid = ClassGroupForm({"name": "错误年级班", "academic_year": "2026秋"})

        self.assertTrue(valid.is_valid())
        self.assertTrue(blank.is_valid())
        self.assertFalse(invalid.is_valid())
        self.assertEqual(invalid.errors["academic_year"], ["年级必须填写四位年份，如：2025。"])
        self.assertEqual(valid.fields["academic_year"].label, "年级")
        self.assertEqual(valid.fields["academic_year"].widget.attrs["placeholder"], "如：2025")
        self.assertEqual(valid.fields["academic_year"].widget.attrs["inputmode"], "numeric")

        self.client.force_login(self.owner)
        response = self.client.get(reverse("courses:classes", args=[self.course.pk]))
        self.assertContains(response, "班级名称或年级")
        self.assertContains(response, "如：2025")
        self.assertNotContains(response, "班级名称或学年")

        historical = ClassGroup.objects.create(
            course=self.course,
            name="历史班级",
            academic_year="2026秋季学期",
        )
        update = ClassGroupUpdateForm(instance=historical)
        invalid_update = ClassGroupUpdateForm(
            {
                "name": historical.name,
                "academic_year": historical.academic_year,
                "is_active": "on",
                "state_token": update.initial["state_token"],
            },
            instance=historical,
        )
        self.assertEqual(update.fields["academic_year"].label, "年级")
        self.assertEqual(update.fields["academic_year"].widget.attrs["placeholder"], "如：2025")
        self.assertFalse(invalid_update.is_valid())
        historical.refresh_from_db()
        self.assertEqual(historical.academic_year, "2026秋季学期")

    def test_class_import_accepts_visible_name_through_preview_and_confirmation(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="EDA1班",
            academic_year="2025",
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["学号", "姓名", "班级"])
        sheet.append(["0001", "xss1", "EDA1班"])
        stream = BytesIO()
        workbook.save(stream)
        upload = SimpleUploadedFile(
            "students.xlsx",
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.client.force_login(self.owner)
        url = reverse("courses:student_import", args=[self.course.pk, class_group.pk])
        template = self.client.get(
            reverse(
                "courses:student_import_template",
                args=[self.course.pk, class_group.pk],
            )
        )
        template_workbook = load_workbook(BytesIO(template.content), data_only=True)
        instructions = "\n".join(
            str(cell.value or "")
            for row in template_workbook["填写说明"].iter_rows()
            for cell in row
        )
        self.assertIn("当前班级名称或完整名称", instructions)

        preview = self.client.post(url, {"file": upload})
        self.assertContains(preview, "名单校验通过")
        self.assertContains(preview, "2025 EDA1班")

        confirmed = self.client.post(url, {"action": "confirm"})

        self.assertRedirects(
            confirmed,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
        self.assertTrue(
            CourseEnrollment.objects.filter(
                course=self.course,
                class_group=class_group,
                student__student_number="0001",
            ).exists()
        )

    def test_removed_class_settings_route_is_not_named(self):
        removed_route_name = "_".join(("class", "rename"))
        with self.assertRaises(NoReverseMatch):
            reverse(f"courses:{removed_route_name}", args=[self.course.pk, 1])

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_class_edit_redirects_to_center_manage_section(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="中心设置班级",
            academic_year="2026",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
        )

        self.assertRedirects(
            response,
            f'{reverse("courses:class_detail", args=[self.course.pk, class_group.pk])}?section=manage',
            fetch_redirect_response=False,
        )

        self.client.force_login(self.outsider)
        self.assertEqual(
            self.client.get(
                reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
            ).status_code,
            404,
        )

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(
                reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
            ).status_code,
            403,
        )

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_edit_renders_an_opaque_state_token(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="令牌班级",
            academic_year="2026",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
        )

        form = response.context["form"]
        self.assertIn("state_token", form.fields)
        token = form.initial["state_token"]
        self.assertContains(response, 'type="hidden" name="state_token"')
        self.assertContains(response, f'action="{self._class_update_url(class_group)}"')
        self.assertTrue(token)
        self.assertNotIn(class_group.name, token)
        self.assertNotIn(class_group.academic_year, token)

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_edit_marks_class_navigation_as_current(self):
        class_group = ClassGroup.objects.create(course=self.course, name="导航班级")
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
        )

        self.assertContains(
            response,
            f'<a class="nav-link active" aria-current="page" href="{reverse("courses:classes", args=[self.course.pk])}">班级</a>',
            html=True,
        )

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_edit_is_get_only_and_does_not_write(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="只读编辑班级",
            academic_year="2026",
            is_active=True,
        )
        self.client.force_login(self.owner)
        token = self._class_edit_state_token(class_group)

        response = self.client.post(
            reverse("courses:class_edit", args=[self.course.pk, class_group.pk]),
            {
                "name": "不应保存的班级",
                "academic_year": "2027",
                "state_token": token,
            },
        )

        self.assertEqual(response.status_code, 405)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "只读编辑班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertTrue(class_group.is_active)
        self.assertFalse(
            AuditLog.objects.filter(action="class.update", object_id=str(class_group.pk)).exists()
        )

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

        response = self.client.post(
            self._class_update_url(class_group),
            {"name": "修订班级", "academic_year": "2027", "state_token": token},
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

        response = self.client.post(
            self._class_update_url(class_group),
            {
                "name": "  中心修订班级  ",
                "academic_year": "2027",
                "state_token": self._class_center_state_token(class_group),
            },
        )

        expected = (
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk])
            + "?section=manage"
        )
        self.assertRedirects(response, expected, fetch_redirect_response=False)
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

        response = self.client.post(
            self._class_update_url(class_group),
            {
                "name": "保持名称班级",
                "academic_year": "2027",
                "is_active": "on",
                "state_token": self._class_edit_state_token(class_group),
            },
        )

        self.assertRedirects(
            response,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
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
            "is_active": "on",
            "state_token": self._class_center_state_token(class_group),
        }
        form = ClassGroupUpdateForm(payload, instance=class_group)

        self.assertFalse(form.is_valid())
        self.assertIn("同一课程和年级中已存在该班级名称。", form.non_field_errors())

        response = self.client.post(
            self._class_update_url(class_group),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_center.html")
        self.assertEqual(response.context["active_section"], "manage")
        self.assertTrue(response.context["class_settings_form"].is_bound)
        self.assertContains(response, "同一课程和年级中已存在该班级名称。")
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "待编辑班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertFalse(
            AuditLog.objects.filter(
                action="class.update", object_id=str(class_group.pk)
            ).exists()
        )

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_update_duplicate_keeps_bound_legacy_edit_form(self):
        ClassGroup.objects.create(
            course=self.course,
            name="旧版已有班级",
            academic_year="2027",
        )
        class_group = ClassGroup.objects.create(
            course=self.course,
            name="旧版待编辑班级",
            academic_year="2026",
        )
        self.client.force_login(self.owner)
        payload = {
            "name": "旧版已有班级",
            "academic_year": "2027",
            "is_active": "on",
            "state_token": self._class_edit_state_token(class_group),
        }

        response = self.client.post(self._class_update_url(class_group), payload)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_edit.html")
        self.assertTrue(response.context["form"].is_bound)
        self.assertContains(response, "同一课程和年级中已存在该班级名称。")
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "旧版待编辑班级")
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

        response = self.client.post(
            self._class_update_url(class_group),
            {
                "name": "过期提交班级",
                "academic_year": "2027",
                "is_active": "on",
                "state_token": stale_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_center.html")
        self.assertEqual(response.context["active_section"], "manage")
        self.assertTrue(response.context["class_settings_form"].is_bound)
        self.assertContains(response, "班级信息已被其他操作更新，请刷新后重试。")
        self.assertContains(response, f'value="{stale_token}"', html=False)
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

        response = self.client.post(
            self._class_update_url(class_group),
            {
                "name": "篡改提交班级",
                "academic_year": "2027",
                "is_active": "on",
                "state_token": tampered_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_center.html")
        self.assertEqual(response.context["active_section"], "manage")
        self.assertTrue(response.context["class_settings_form"].is_bound)
        self.assertContains(response, "班级信息已被其他操作更新，请刷新后重试。")
        self.assertContains(response, f'value="{tampered_token}"', html=False)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "签名班级")
        self.assertEqual(class_group.academic_year, "2026")
        self.assertTrue(class_group.is_active)
        self.assertFalse(
            AuditLog.objects.filter(action="class.update", object_id=str(class_group.pk)).exists()
        )

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

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_edit_and_update_enforce_methods_scope_and_csrf(self):
        class_group = ClassGroup.objects.create(course=self.course, name="受限班级")
        edit_url = reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
        update_url = self._class_update_url(class_group)

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertEqual(self.client.post(update_url, {}).status_code, 404)

        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.assertEqual(self.client.get(update_url).status_code, 405)

        self.client.force_login(self.owner)
        token = self._class_edit_state_token(class_group)
        self.assertEqual(
            self.client.post(
                edit_url,
                {"name": "不应保存", "academic_year": "2026", "state_token": token},
            ).status_code,
            405,
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(
            csrf_client.post(
                update_url,
                {"name": "无令牌", "academic_year": "2026", "is_active": "on"},
            ).status_code,
            403,
        )

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
                    self.assertEqual(
                        self.client.get(
                            reverse(
                                "courses:class_edit",
                                args=[self.course.pk, class_group.pk],
                            )
                        ).status_code,
                        403,
                    )
                    response = self.client.post(
                        self._class_update_url(class_group),
                        {
                            "name": "归档后不应修改",
                            "academic_year": "2027",
                            "is_active": "on",
                            "state_token": "invalid",
                        },
                    )
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
