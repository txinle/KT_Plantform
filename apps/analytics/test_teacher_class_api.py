import os
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User


@override_settings(CLASS_CENTER_ENABLED=True)
class TeacherClassApiTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="teacher-class-api-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="班级 API 课程",
            code="CLASS-API",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            owner=self.owner,
            course=self.course,
            name="一班",
            academic_year="2026",
        )
        self.student = User.objects.create_user(
            username="CLASS-API-S001",
            student_number="CLASS-API-S001",
            display_name="班级 API 学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.client.force_login(self.owner)

    def test_class_collection_is_stably_paginated_at_twenty(self):
        for index in range(21):
            ClassGroup.objects.create(
                owner=self.owner,
                course=self.course,
                name=f"测试班 {index:02d}",
                academic_year="2026",
                is_active=index != 20,
            )

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(
            payload["page"],
            {
                "number": 1,
                "size": 20,
                "total_items": 22,
                "total_pages": 2,
                "start": 1,
                "end": 20,
            },
        )
        self.assertEqual(payload["course"]["id"], self.course.pk)
        detail_response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/",
            {"q": "一班"},
        )
        class_item = detail_response.json()["items"][0]
        self.assertEqual(class_item["active_student_count"], 1)
        self.assertEqual(
            class_item["detail_url"],
            reverse(
                "courses:class_detail",
                args=[self.course.pk, self.class_group.pk],
            ),
        )
        self.assertNotIn("owner_id", class_item)

    def test_class_collection_search_applies_before_pagination(self):
        ClassGroup.objects.create(
            owner=self.owner,
            course=self.course,
            name="唯一命中班",
            academic_year="2025",
        )

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/",
            {"q": "唯一命中"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["page"]["total_items"], 1)
        self.assertEqual(response.json()["items"][0]["name"], "唯一命中班")

    def test_class_center_exposes_authoritative_empty_state_and_navigation(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/center/"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["course"]["id"], self.course.pk)
        self.assertEqual(payload["class_group"]["id"], self.class_group.pk)
        self.assertEqual(payload["class_group"]["active_student_count"], 1)
        self.assertEqual(payload["scope"]["task_count"], 0)
        self.assertEqual(payload["overview"]["roster_count"], 1)
        self.assertEqual(payload["overview"]["correct_rate"]["state"], "no_data")
        self.assertEqual(payload["overview"]["correct_rate"]["percent_display"], "—")
        self.assertEqual(payload["students"]["page"]["size"], 20)
        self.assertEqual(payload["students"]["page"]["total_items"], 1)
        self.assertEqual(payload["students"]["items"][0]["student_id"], self.student.pk)
        self.assertIn("section=manage", payload["actions"]["manage_url"])
        self.assertNotIn("session", payload)
        self.assertNotIn("attempt", payload)

    def test_unknown_or_inaccessible_scope_is_hidden_as_404(self):
        outsider = User.objects.create_user(
            username="teacher-class-api-outsider",
            role=User.Role.TEACHER,
        )
        self.client.force_login(outsider)

        collection = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
        )
        center = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/center/"
        )

        self.assertEqual(collection.status_code, 404)
        self.assertEqual(center.status_code, 404)

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_center_api_is_unavailable_when_product_center_is_disabled(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/center/"
        )

        self.assertEqual(response.status_code, 404)


@override_settings(
    CLASS_CENTER_ENABLED=True,
    DEBUG=True,
    VITE_DEV_SERVER_URL="http://localhost:5173",
)
class TeacherClassVueShellTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher-class-shell-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="班级 Shell 课程",
            code="CLASS-SHELL",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            owner=self.teacher,
            course=self.course,
            name="试点班",
            academic_year="2026",
        )
        self.class_list_url = reverse("courses:classes", args=[self.course.pk])
        self.class_detail_url = reverse(
            "courses:class_detail",
            args=[self.course.pk, self.class_group.pk],
        )
        self.client.force_login(self.teacher)

    def rollout_env(self, **values):
        defaults = {
            "VUE_TEACHER_CLASSES_ENABLED": "0",
            "VUE_TEACHER_CLASSES_COURSE_IDS": "",
            "VUE_TEACHER_CLASSES_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict(os.environ, defaults)

    def test_flag_off_preserves_class_templates(self):
        with self.rollout_env():
            class_list = self.client.get(self.class_list_url)
            class_detail = self.client.get(self.class_detail_url)

        self.assertTemplateUsed(class_list, "teacher/course_class_list.html")
        self.assertTemplateUsed(class_detail, "teacher/course_class_center.html")
        self.assertNotContains(class_list, 'id="kt-app"')
        self.assertNotContains(class_detail, 'id="kt-app"')

    def test_pilot_course_receives_vue_shell_on_existing_read_routes(self):
        with self.rollout_env(
            VUE_TEACHER_CLASSES_ENABLED="1",
            VUE_TEACHER_CLASSES_COURSE_IDS=str(self.course.pk),
        ):
            class_list = self.client.get(self.class_list_url)
            class_detail = self.client.get(self.class_detail_url)

        for response in (class_list, class_detail):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="kt-app"')
            self.assertContains(response, 'data-module="teacher-classes"')

    def test_nonpilot_course_and_manage_section_remain_django_documents(self):
        other_course = Course.objects.create(
            owner=self.teacher,
            name="非试点课程",
            code="CLASS-SHELL-OTHER",
            term="2026 秋",
        )
        other_class = ClassGroup.objects.create(
            owner=self.teacher,
            course=other_course,
            name="旧页面班",
            academic_year="2026",
        )
        with self.rollout_env(
            VUE_TEACHER_CLASSES_ENABLED="1",
            VUE_TEACHER_CLASSES_COURSE_IDS=str(self.course.pk),
        ):
            nonpilot = self.client.get(
                reverse("courses:class_detail", args=[other_course.pk, other_class.pk])
            )
            manage = self.client.get(self.class_detail_url, {"section": "manage"})

        self.assertTemplateUsed(nonpilot, "teacher/course_class_center.html")
        self.assertTemplateUsed(manage, "teacher/course_class_center.html")
        self.assertNotContains(nonpilot, 'id="kt-app"')
        self.assertNotContains(manage, 'id="kt-app"')

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_product_flag_off_wins_over_vue_rollout(self):
        with self.rollout_env(VUE_TEACHER_CLASSES_ENABLED="1"):
            response = self.client.get(self.class_detail_url)

        self.assertTemplateUsed(response, "teacher/course_class_detail.html")
        self.assertNotContains(response, 'id="kt-app"')
