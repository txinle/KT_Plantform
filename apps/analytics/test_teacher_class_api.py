import json
import os
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.audit.models import AuditLog


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

    def post_json(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

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
        self.assertEqual(payload["capabilities"], {"can_create": True, "can_manage": True})
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

    def test_class_create_reuses_form_validation_and_returns_class_summary(self):
        endpoint = f"/api/v1/teacher/courses/{self.course.pk}/classes/"

        invalid = self.post_json(
            endpoint,
            {"name": "", "academic_year": "2026秋"},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("name", invalid.json()["error"]["fields"])
        self.assertIn("academic_year", invalid.json()["error"]["fields"])

        created = self.post_json(
            endpoint,
            {"name": "JSON 创建班", "academic_year": "2027"},
        )
        self.assertEqual(created.status_code, 201)
        class_group = ClassGroup.objects.get(
            course=self.course,
            name="JSON 创建班",
        )
        self.assertEqual(class_group.owner_id, self.course.owner_id)
        self.assertEqual(
            created.json()["class_group"],
            {
                "id": class_group.pk,
                "name": "JSON 创建班",
                "academic_year": "2027",
                "is_active": True,
                "active_student_count": 0,
                "detail_url": reverse(
                    "courses:class_detail",
                    args=[self.course.pk, class_group.pk],
                ),
            },
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="class.create", object_id=str(class_group.pk)
            ).exists()
        )

    def test_archived_course_reports_create_capability_and_rejects_creation(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        endpoint = f"/api/v1/teacher/courses/{self.course.pk}/classes/"

        collection = self.client.get(endpoint)
        self.assertEqual(collection.status_code, 200)
        self.assertEqual(collection.json()["capabilities"], {"can_create": False, "can_manage": False})

        rejected = self.post_json(
            endpoint,
            {"name": "归档课程班", "academic_year": "2027"},
        )
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(
            ClassGroup.objects.filter(course=self.course, name="归档课程班").exists()
        )

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
        creation = self.post_json(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/",
            {"name": "越权创建班", "academic_year": "2027"},
        )

        self.assertEqual(collection.status_code, 404)
        self.assertEqual(center.status_code, 404)
        self.assertEqual(creation.status_code, 404)

    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_class_center_api_is_unavailable_when_product_center_is_disabled(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/center/"
        )

        self.assertEqual(response.status_code, 404)
