from types import SimpleNamespace
from django.test import Client, SimpleTestCase, TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import Course, User
from apps.api.exceptions import ApiConflict, api_exception_handler
from apps.api.permissions import CourseManagerPermission, RolePermission


class ApiPermissionBaseTests(SimpleTestCase):
    def request_for(self, user):
        request = APIRequestFactory().get("/api/v1/resource/")
        force_authenticate(request, user=user)
        request.user = user
        return request

    def test_role_permission_uses_the_view_contract(self):
        permission = RolePermission()
        teacher = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            is_superuser=False,
            role=User.Role.TEACHER,
        )
        view = SimpleNamespace(allowed_roles=(User.Role.TEACHER,))

        self.assertTrue(permission.has_permission(self.request_for(teacher), view))
        teacher.role = User.Role.STUDENT
        self.assertFalse(permission.has_permission(self.request_for(teacher), view))

    def test_course_permission_uses_domain_course_authority(self):
        permission = CourseManagerPermission()
        user = SimpleNamespace(is_authenticated=True, is_active=True)
        course = SimpleNamespace(can_manage=lambda candidate: candidate is user)

        self.assertTrue(permission.has_object_permission(self.request_for(user), None, course))
        self.assertFalse(
            permission.has_object_permission(
                self.request_for(SimpleNamespace(is_authenticated=True, is_active=True)),
                None,
                course,
            )
        )


class ApiErrorContractTests(SimpleTestCase):
    def setUp(self):
        self.request = SimpleNamespace(request_id="request-1")

    def test_permission_denied_has_a_stable_error_shape(self):
        response = api_exception_handler(
            PermissionDenied("不允许执行该操作。"),
            {"request": self.request},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "PERMISSION_DENIED")
        self.assertEqual(response.data["error"]["request_id"], "request-1")

    def test_conflict_preserves_the_frozen_business_code(self):
        response = api_exception_handler(
            ApiConflict(
                "发布计划已更新。",
                code="PUBLISH_PLAN_STALE",
                fields={"plan_revision": 4},
            ),
            {"request": self.request},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "PUBLISH_PLAN_STALE")
        self.assertEqual(response.data["error"]["message"], "发布计划已更新。")
        self.assertEqual(response.data["error"]["fields"], {"plan_revision": 4})


class ApiCsrfContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="csrf-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_missing_csrf_is_a_structured_api_error(self):
        response = self.client.post(
            "/api/v1/auth/session/",
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "CSRF_FAILED")
        self.assertEqual(response.json()["error"]["fields"], {})
