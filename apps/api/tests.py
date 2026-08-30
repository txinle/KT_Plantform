from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.accounts.models import Course, User
from apps.api.pagination import FixedPageNumberPagination


class ApiFoundationContractTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="api-owner",
            password="test-password",
            role=User.Role.TEACHER,
            display_name="课程负责人",
        )
        self.other_teacher = User.objects.create_user(
            username="api-other",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="API 试点课程",
            code="API-001",
            term="2026-Autumn",
            owner=self.owner,
        )

    def test_anonymous_session_uses_standard_error_and_request_id(self):
        response = self.client.get("/api/v1/auth/session/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        self.assertEqual(
            response.json()["error"]["request_id"],
            response.headers["X-Request-ID"],
        )

    def test_session_returns_only_authenticated_user_context(self):
        self.client.force_login(self.owner)

        response = self.client.get("/api/v1/auth/session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "user": {
                    "id": self.owner.pk,
                    "role": User.Role.TEACHER,
                    "display_name": "课程负责人",
                }
            },
        )

    def test_csrf_bootstrap_is_available_before_authentication(self):
        response = self.client.get("/api/v1/auth/csrf/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["csrf_token"])
        self.assertIn("csrftoken", response.cookies)

    def test_course_context_is_visible_to_its_manager(self):
        self.client.force_login(self.owner)

        response = self.client.get(f"/api/v1/courses/{self.course.pk}/context/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": self.course.pk,
                "name": "API 试点课程",
                "code": "API-001",
                "term": "2026-Autumn",
                "status": Course.Status.ACTIVE,
            },
        )

    def test_invisible_course_uses_standard_not_found_error(self):
        self.client.force_login(self.other_teacher)

        response = self.client.get(f"/api/v1/courses/{self.course.pk}/context/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")
        self.assertEqual(response.json()["error"]["fields"], {})

    def test_openapi_schema_exposes_only_the_foundation_endpoints(self):
        self.client.force_login(self.owner)

        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/api/v1/auth/session/", paths)
        self.assertIn("/api/v1/auth/csrf/", paths)
        self.assertIn("/api/v1/courses/{course_id}/context/", paths)
        self.assertIn("/api/v1/health/", paths)
        self.assertIn(
            "SessionAuth",
            response.json()["components"]["securitySchemes"],
        )

    def test_fixed_pagination_uses_the_shared_collection_shape(self):
        request = Request(APIRequestFactory().get("/api/v1/items/", {"page": 2}))
        paginator = FixedPageNumberPagination()

        items = paginator.paginate_queryset(list(range(25)), request)
        response = paginator.get_paginated_response(items)

        self.assertEqual(items, list(range(20, 25)))
        self.assertEqual(
            response.data["page"],
            {
                "number": 2,
                "size": 20,
                "total_items": 25,
                "total_pages": 2,
                "start": 21,
                "end": 25,
            },
        )
