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
