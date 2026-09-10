import json
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User


@override_settings(
    ALLOWED_HOSTS=["testserver", "ktedu.online", "203.0.113.10", "localhost"],
    CANONICAL_PUBLIC_ORIGIN="https://ktedu.online",
)
class CanonicalPublicOriginMiddlewareTests(TestCase):
    def test_canonical_host_is_not_redirected(self):
        response = self.client.get(
            reverse("api-csrf"),
            HTTP_HOST="ktedu.online",
            secure=True,
        )

        self.assertEqual(response.status_code, 200)

    def test_http_canonical_host_redirects_to_the_https_origin(self):
        response = self.client.get(reverse("api-csrf"), HTTP_HOST="ktedu.online")

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response["Location"], "https://ktedu.online/api/v1/auth/csrf/")

    def test_noncanonical_host_redirects_get_and_preserves_path_and_query(self):
        response = self.client.get(
            f'{reverse("api-csrf")}?next=%2Fstudent%2Fsessions%2F9%2F',
            HTTP_HOST="203.0.113.10",
        )

        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response["Location"],
            "https://ktedu.online/api/v1/auth/csrf/?next=%2Fstudent%2Fsessions%2F9%2F",
        )

    def test_noncanonical_head_request_uses_the_same_permanent_redirect(self):
        response = self.client.head(reverse("api-csrf"), HTTP_HOST="203.0.113.10")

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response["Location"], "https://ktedu.online/api/v1/auth/csrf/")

    def test_health_and_internal_hosts_are_not_redirected(self):
        health = self.client.get(reverse("health"), HTTP_HOST="203.0.113.10")
        internal = self.client.get(reverse("api-csrf"), HTTP_HOST="localhost")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(internal.status_code, 200)

    def test_non_safe_request_is_not_redirected(self):
        response = self.client.post(
            reverse("api-login"),
            data=json.dumps({"username": "missing", "password": "missing"}),
            content_type="application/json",
            HTTP_HOST="203.0.113.10",
        )

        self.assertNotEqual(response.status_code, 308)


@override_settings(
    CANONICAL_PUBLIC_ORIGIN="",
    STUDENT_SESSION_IDLE_TIMEOUT_SECONDS=1800,
)
class StudentSessionIdleTimeoutMiddlewareTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="session-student",
            password="test-password",
            role=User.Role.STUDENT,
            student_number="SESSION-001",
        )
        self.teacher = User.objects.create_user(
            username="session-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.admin = User.objects.create_superuser(
            username="session-admin",
            password="test-password",
            role=User.Role.ADMIN,
        )

    def test_authenticated_student_request_renews_a_30_minute_expiry(self):
        self.client.force_login(self.student)
        session = self.client.session
        session.set_expiry(60)
        session.save()

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session.get_expiry_age(), 1800)

    def test_student_login_response_sets_the_idle_expiry_immediately(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_response = csrf_client.get(reverse("api-csrf"))

        response = csrf_client.post(
            reverse("api-login"),
            data=json.dumps(
                {"username": self.student.username, "password": "test-password"}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrf_token"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(csrf_client.session.get_expiry_age(), 1800)

    def test_teacher_and_admin_session_expiry_is_unchanged(self):
        for user in (self.teacher, self.admin):
            with self.subTest(role=user.role):
                client = Client()
                client.force_login(user)
                session = client.session
                session.set_expiry(600)
                session.save()

                response = client.get(reverse("api-session"))

                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(client.session.get_expiry_age(), 600)
                self.assertGreater(client.session.get_expiry_age(), 590)

    def test_expired_student_session_returns_401(self):
        self.client.force_login(self.student)
        session_key = self.client.session.session_key
        Session.objects.filter(session_key=session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1)
        )

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 401)
