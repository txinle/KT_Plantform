from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Course, DataPurgeRequest, User


class DataGovernanceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="gov-teacher", role=User.Role.TEACHER)
        self.other = User.objects.create_user(username="gov-other", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="治理课程", code="GOV", term="2026")

    def test_settings_exposes_identity_governance_and_purge_preview(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("courses:settings", args=[self.course.pk]))
        body = response.content.decode("utf-8")
        self.assertIn("identity-governance", body)
        self.assertIn("受控数据清除", body)
        self.assertIn("不会删除课程账号", body)

    def test_purge_request_is_scoped_to_course_manager(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("analytics:purge_request_create", args=[self.course.pk]),
            {"confirmation": "PURGE", "reason_code": "research_withdrawal"},
        )
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("analytics:purge_request_create", args=[self.course.pk]),
            {"confirmation": "PURGE", "reason_code": "research_withdrawal"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DataPurgeRequest.objects.count(), 1)
