from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User


class ClassCenterViewTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="class-center-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="班级中心视图课程",
            code="CLASS-CENTER-VIEW",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="视图实验班",
            academic_year="2026",
        )
        self.student = User.objects.create_user(
            username="VIEW-S001",
            student_number="VIEW-S001",
            display_name="视图学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.url = reverse(
            "courses:class_detail",
            args=[self.course.pk, self.class_group.pk],
        )

    def test_disabled_flag_preserves_the_roster_management_page(self):
        self.client.force_login(self.owner)

        with override_settings(CLASS_CENTER_ENABLED=False):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_detail.html")
        self.assertContains(response, "本页只管理其在当前课程中的选课和班级关系")
        self.assertNotContains(response, "实验分析")

    def test_entry_labels_follow_the_class_center_flag(self):
        self.client.force_login(self.owner)
        class_list_url = reverse("courses:classes", args=[self.course.pk])
        course_detail_url = reverse("courses:detail", args=[self.course.pk])

        with override_settings(CLASS_CENTER_ENABLED=False):
            class_list = self.client.get(class_list_url)
            course_detail = self.client.get(course_detail_url)

        self.assertContains(class_list, "管理学生")
        self.assertNotContains(class_list, "进入班级中心")
        self.assertNotContains(class_list, "编辑班级")
        self.assertNotContains(class_list, "编辑名称")
        self.assertContains(course_detail, "班级与学生")
        self.assertNotContains(course_detail, "班级教学分析")

        with override_settings(CLASS_CENTER_ENABLED=True):
            class_list = self.client.get(class_list_url)
            course_detail = self.client.get(course_detail_url)

        self.assertContains(class_list, "进入班级中心")
        self.assertNotContains(class_list, ">管理学生<", html=False)
        self.assertNotContains(class_list, "编辑班级")
        self.assertNotContains(class_list, "编辑名称")
        self.assertContains(class_list, "删除班级")
        self.assertContains(course_detail, "班级教学分析")
        self.assertContains(course_detail, class_list_url)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_enabled_flag_opens_overview_with_four_semantic_tabs(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_center.html")
        self.assertEqual(response.context["active_section"], "overview")
        for label in ("概览", "学生表现", "实验分析", "班级管理"):
            self.assertContains(response, label)
        self.assertContains(response, "班级学生")
        self.assertContains(response, "参与学生")
        self.assertContains(response, "总体正确率")
        self.assertContains(response, "实验完成率")
        self.assertContains(response, 'name="experiment_id"', html=False)
        self.assertNotContains(response, "平均正确率")
        for forbidden in ("ExperimentVersion", "Session", "Attempt", "snapshot UUID"):
            self.assertNotContains(response, forbidden)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_sections_keep_management_forms_and_server_side_student_filters(self):
        self.client.force_login(self.owner)

        students = self.client.get(
            self.url,
            {"section": "students", "q": "VIEW-S001", "sort": "correct_rate"},
        )
        management = self.client.get(self.url, {"section": "manage"})
        experiments = self.client.get(self.url, {"section": "experiments"})

        self.assertEqual(students.context["active_section"], "students")
        self.assertEqual(students.context["class_center"].students.total_count, 1)
        self.assertContains(students, "VIEW-S001")
        self.assertContains(students, 'name="experiment_id"', html=False)
        self.assertEqual(experiments.context["active_section"], "experiments")
        self.assertContains(experiments, "选择实验")
        self.assertEqual(management.context["active_section"], "manage")
        self.assertContains(management, 'class="reset-password-form"', html=False)
        self.assertContains(management, "data-student-remove-trigger", html=False)
        self.assertContains(management, reverse(
            "courses:move_student",
            args=[self.course.pk, self.class_group.pk, self.student.pk],
        ))

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_manage_section_places_class_settings_before_member_management(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url, {"section": "manage"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_center.html")
        self.assertEqual(response.context["active_section"], "manage")
        form = response.context["class_settings_form"]
        self.assertFalse(form.is_bound)
        self.assertTrue(form.initial["state_token"])
        self.assertContains(response, "班级设置")
        self.assertContains(
            response,
            f'action="{reverse("courses:class_update", args=[self.course.pk, self.class_group.pk])}"',
        )
        self.assertContains(response, 'name="state_token"', html=False)
        self.assertContains(response, 'name="name"', html=False)
        self.assertContains(response, 'name="academic_year"', html=False)
        self.assertContains(response, 'name="is_active"', html=False)
        self.assertContains(response, "停用后不能新增、导入、复制或转入学生")
        self.assertContains(response, "导入学生")
        self.assertNotContains(response, "编辑班级")
        self.assertLess(
            response.content.decode().index("班级设置"),
            response.content.decode().index("班级学生"),
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_archived_course_manage_section_is_read_only(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.owner)

        response = self.client.get(self.url, {"section": "manage"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context.get("class_settings_form"))
        self.assertContains(response, "课程已归档，班级设置只读")
        self.assertNotContains(
            response,
            reverse("courses:class_update", args=[self.course.pk, self.class_group.pk]),
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_inactive_class_keeps_settings_but_hides_student_import(self):
        self.class_group.is_active = False
        self.class_group.save(update_fields=["is_active"])
        self.client.force_login(self.owner)

        response = self.client.get(self.url, {"section": "manage"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["class_settings_form"])
        self.assertContains(response, "班级已停用。")
        self.assertNotContains(response, "导入学生")

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_owner_collaborator_and_admin_can_read_but_outsider_gets_404(self):
        collaborator = User.objects.create_user(
            username="class-center-collaborator",
            role=User.Role.TEACHER,
        )
        outsider = User.objects.create_user(
            username="class-center-outsider",
            role=User.Role.TEACHER,
        )
        admin = User.objects.create_user(
            username="class-center-admin",
            role=User.Role.ADMIN,
        )
        self.course.teacher_links.create(teacher=collaborator)

        for reader in (self.owner, collaborator, admin):
            self.client.force_login(reader)
            self.assertEqual(self.client.get(self.url).status_code, 200)

        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_wrong_course_class_pair_and_unknown_section_fail_closed(self):
        other_course = Course.objects.create(
            owner=self.owner,
            name="其他课程",
            code="OTHER-CLASS-CENTER",
            term="2026 秋",
        )
        other_class = ClassGroup.objects.create(
            course=other_course,
            name="其他班级",
            academic_year="2026",
        )
        wrong_pair = reverse(
            "courses:class_detail",
            args=[self.course.pk, other_class.pk],
        )
        self.client.force_login(self.owner)

        self.assertEqual(self.client.get(wrong_pair).status_code, 404)
        response = self.client.get(self.url, {"section": "not-a-section"})
        self.assertEqual(response.status_code, 404)
        invalid_experiment = self.client.get(
            self.url,
            {"section": "manage", "experiment_id": "not-an-integer"},
        )
        self.assertEqual(invalid_experiment.status_code, 404)
