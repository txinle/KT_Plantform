import json
import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Course, CourseTeacher, User
from apps.audit.models import AuditLog


class TeacherCourseApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="teacher-course-owner",
            display_name="课程负责人",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="teacher-course-collaborator",
            display_name="协作教师",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="teacher-course-outsider",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="teacher-course-student",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="课程 API 试点",
            code="COURSE-API",
            term="2026 秋",
            description="用于验证教师课程工作区。",
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.collection_url = "/api/v1/teacher/courses/"
        self.detail_url = f"{self.collection_url}{self.course.pk}/"
        self.settings_url = f"{self.detail_url}settings/"
        self.client.force_login(self.owner)

    def post_json(self, url, data):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_collection_exposes_accessible_course_summaries_and_existing_links(self):
        inaccessible = Course.objects.create(
            owner=self.outsider,
            name="不应暴露的课程",
            code="HIDDEN",
            term="2026 秋",
        )

        response = self.client.get(self.collection_url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["create_url"], reverse("courses:create"))
        self.assertEqual([item["id"] for item in payload["items"]], [self.course.pk])
        item = payload["items"][0]
        self.assertEqual(item["owner_name"], self.owner.shown_name)
        self.assertEqual(item["detail_url"], reverse("courses:detail", args=[self.course.pk]))
        self.assertNotIn(inaccessible.pk, [item["id"] for item in payload["items"]])

    def test_create_reuses_course_form_validation_and_existing_audit(self):
        invalid = self.post_json(
            self.collection_url,
            {"name": "", "code": "", "term": "", "description": ""},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("name", invalid.json()["error"]["fields"])

        response = self.post_json(
            self.collection_url,
            {
                "name": "大学物理",
                "code": "PHYS101",
                "term": "2026 秋",
                "description": "课程说明",
            },
        )

        self.assertEqual(response.status_code, 201)
        course = Course.objects.get(code="PHYS101")
        self.assertEqual(course.owner, self.owner)
        self.assertEqual(response.json()["course"]["detail_url"], reverse("courses:detail", args=[course.pk]))
        self.assertTrue(
            AuditLog.objects.filter(action="course.create", object_id=str(course.pk)).exists()
        )

    def test_detail_and_settings_preserve_manager_scope_and_form_state_contract(self):
        detail = self.client.get(self.detail_url)
        settings = self.client.get(self.settings_url)

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["course"]["settings_url"], reverse("courses:settings", args=[self.course.pk]))
        self.assertEqual(detail.json()["metrics"], {
            "class_count": 0,
            "bank_count": 0,
            "experiment_count": 0,
            "student_count": 0,
        })
        self.assertEqual(settings.status_code, 200)
        state = settings.json()["form"]
        self.assertEqual(
            settings.json()["capabilities"],
            {"can_update": True, "can_archive": True},
        )
        self.assertEqual(state["teachers"], [self.collaborator.pk])
        self.assertIn(self.collaborator.pk, [option["id"] for option in state["teacher_options"]])

        updated = self.post_json(
            self.settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "已通过现有设置表单更新。",
                "teachers": [self.collaborator.pk],
                "state_token": state["state_token"],
            },
        )

        self.assertEqual(updated.status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "已通过现有设置表单更新。")
        self.assertNotEqual(updated.json()["form"]["state_token"], state["state_token"])
        self.assertTrue(
            AuditLog.objects.filter(action="course.update", object_id=str(self.course.pk)).exists()
        )

    def test_stale_settings_token_does_not_overwrite_current_course(self):
        state_token = self.client.get(self.settings_url).json()["form"]["state_token"]
        Course.objects.filter(pk=self.course.pk).update(description="其他管理者已经更新")

        response = self.post_json(
            self.settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "陈旧浏览器内容",
                "teachers": [self.collaborator.pk],
                "state_token": state_token,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("__all__", response.json()["error"]["fields"])
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "其他管理者已经更新")

    def test_archived_course_reports_read_only_capabilities(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        response = self.client.get(self.settings_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["capabilities"],
            {"can_update": False, "can_archive": False},
        )

    def test_role_and_course_access_are_server_authorized(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.collection_url).status_code, 403)

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)
        self.assertEqual(self.client.get(self.settings_url).status_code, 404)


@override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
class TeacherCourseVueShellTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher-course-shell-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="课程 Shell 试点",
            code="COURSE-SHELL",
            term="2026 秋",
        )
        self.urls = (
            reverse("courses:list"),
            reverse("courses:create"),
            reverse("courses:detail", args=[self.course.pk]),
            reverse("courses:settings", args=[self.course.pk]),
        )
        self.client.force_login(self.teacher)

    def rollout_env(self, **values):
        defaults = {
            "VUE_TEACHER_COURSES_ENABLED": "0",
            "VUE_TEACHER_COURSES_COURSE_IDS": "",
            "VUE_TEACHER_COURSES_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict(os.environ, defaults)

    def test_flag_off_preserves_existing_documents(self):
        with self.rollout_env():
            course_list, create, detail, settings = [self.client.get(url) for url in self.urls]

        self.assertTemplateUsed(course_list, "teacher/course_list.html")
        self.assertTemplateUsed(create, "teacher/course_form.html")
        self.assertTemplateUsed(detail, "teacher/course_detail.html")
        self.assertTemplateUsed(settings, "teacher/course_settings.html")
        self.assertNotContains(course_list, 'id="kt-app"')

    def test_user_pilot_receives_vue_shell_on_all_four_existing_get_routes(self):
        with self.rollout_env(
            VUE_TEACHER_COURSES_ENABLED="1",
            VUE_TEACHER_COURSES_USER_IDS=str(self.teacher.pk),
        ):
            responses = [self.client.get(url) for url in self.urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="kt-app"')
            self.assertContains(response, 'data-module="teacher-courses"')

    def test_post_remains_the_existing_django_form_flow_while_vue_is_enabled(self):
        with self.rollout_env(
            VUE_TEACHER_COURSES_ENABLED="1",
            VUE_TEACHER_COURSES_USER_IDS=str(self.teacher.pk),
        ):
            response = self.client.post(
                reverse("courses:create"),
                {
                    "name": "仍由 Django 表单创建",
                    "code": "FALLBACK",
                    "term": "2026 秋",
                    "description": "",
                },
            )

        created = Course.objects.get(code="FALLBACK")
        self.assertRedirects(response, reverse("courses:detail", args=[created.pk]))
