import json

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User


class AuthenticationApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="auth-teacher",
            password="test-password",
            role=User.Role.TEACHER,
            display_name="认证教师",
        )
        self.student = User.objects.create_user(
            username="S20260001",
            password="test-password",
            role=User.Role.STUDENT,
            display_name="认证学生",
            student_number="S20260001",
        )

    def test_session_projects_the_authenticated_identity_and_server_home(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "user": {
                    "id": self.student.pk,
                    "role": User.Role.STUDENT,
                    "display_name": "认证学生",
                    "username": "S20260001",
                    "student_number": "S20260001",
                    "home_url": reverse("assessment:dashboard"),
                    "must_change_password": False,
                }
            },
        )

    def test_login_rejects_requests_without_a_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("api-login"),
            data=json.dumps({"username": self.teacher.username, "password": "test-password"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "CSRF_FAILED")

    def test_login_authenticates_with_a_csrf_token_and_returns_session_context(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_response = csrf_client.get(reverse("api-csrf"))

        response = csrf_client.post(
            reverse("api-login"),
            data=json.dumps({"username": self.teacher.username, "password": "test-password"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrf_token"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["user"],
            {
                "id": self.teacher.pk,
                "role": User.Role.TEACHER,
                "display_name": "认证教师",
                "username": "auth-teacher",
                "student_number": None,
                "home_url": reverse("courses:list"),
                "must_change_password": False,
            },
        )
        self.assertEqual(csrf_client.get(reverse("api-session")).status_code, 200)

    def test_logout_requires_csrf_then_invalidates_the_session(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)

        forbidden = csrf_client.post(reverse("api-logout"))
        csrf_response = csrf_client.get(reverse("api-csrf"))
        response = csrf_client.post(
            reverse("api-logout"),
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrf_token"],
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["error"]["code"], "CSRF_FAILED")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(csrf_client.get(reverse("api-session")).status_code, 401)
