from apps.api.test_support import domain_payload
import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User


class AuthenticationApiTests(TestCase):
    @override_settings(STUDENT_COURSE_NAVIGATION_ENABLED=False, STUDENT_COURSE_NAVIGATION_USER_IDS=frozenset({'*'}))
    def test_navigation_optimization_requires_server_switch_and_allowlist(self):
        self.client.force_login(self.student)
        def navigation():
            return domain_payload(self.client.get('/api/v1/auth/bootstrap/'))['client_navigation']
        self.assertEqual(navigation(), {'student_courses': False, 'course_ids': []})
        with override_settings(STUDENT_COURSE_NAVIGATION_ENABLED=True,
                               STUDENT_COURSE_NAVIGATION_USER_IDS=frozenset(),
                               STUDENT_COURSE_NAVIGATION_COURSE_IDS=frozenset()):
            self.assertEqual(navigation(), {'student_courses': False, 'course_ids': []})
            with override_settings(STUDENT_COURSE_NAVIGATION_COURSE_IDS=frozenset({'7'})):
                self.assertEqual(navigation(), {'student_courses': True, 'course_ids': ['7']})
            with override_settings(STUDENT_COURSE_NAVIGATION_USER_IDS=frozenset({str(self.student.pk)})):
                self.assertEqual(navigation(), {'student_courses': True, 'course_ids': ['*']})
                self.client.force_login(self.teacher)
                self.assertEqual(navigation(), {'student_courses': False, 'course_ids': []})

    @override_settings(STUDENT_COURSE_NAVIGATION_ENABLED=True, STUDENT_COURSE_NAVIGATION_USER_IDS=frozenset({'*'}))
    def test_login_projects_navigation_permission_with_rotated_context(self):
        response = self.client.post(reverse('api-login'), json.dumps({
            'username': self.student.username, 'password': 'test-password',
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['client_navigation'], {'student_courses': True, 'course_ids': ['*']})

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

    def test_bootstrap_returns_anonymous_csrf_in_one_uncacheable_response(self):
        response = self.client.get('/api/v1/auth/bootstrap/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(domain_payload(response)['user'])
        self.assertTrue(domain_payload(response)['csrf_token'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_bootstrap_returns_authenticated_identity_without_a_session_probe(self):
        self.client.force_login(self.student)
        response = self.client.get('/api/v1/auth/bootstrap/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['user']['id'], self.student.pk)
        self.assertTrue(domain_payload(response)['csrf_token'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_login_returns_rotated_csrf_that_can_logout_without_another_get(self):
        client = Client(enforce_csrf_checks=True)
        initial = domain_payload(client.get(reverse('api-csrf')))['csrf_token']
        response = client.post(reverse('api-login'),
                               json.dumps({'username': self.student.username, 'password': 'test-password'}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=initial)
        self.assertEqual(response.status_code, 200)
        rotated = domain_payload(response).get('csrf_token')
        self.assertTrue(rotated)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(client.post(reverse('api-logout'), HTTP_X_CSRFTOKEN=initial).status_code, 403)
        self.assertEqual(client.post(reverse('api-logout'), HTTP_X_CSRFTOKEN=rotated).status_code, 204)

    def test_authentication_success_and_error_responses_cannot_be_cached(self):
        for path in ('/api/v1/auth/csrf/', '/api/v1/auth/session/', '/api/v1/auth/research-consent/'):
            with self.subTest(path=path):
                self.assertIn('no-store', self.client.get(path)['Cache-Control'])

    def test_session_projects_the_authenticated_identity_and_server_home(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            domain_payload(response),
            {
                "user": {
                    "id": self.student.pk,
                    "role": User.Role.STUDENT,
                    "display_name": "认证学生",
                    "username": "S20260001",
                    "student_number": "S20260001",
                    "home_url": reverse("assessment:dashboard"),
                    "uses_initial_password": False,
                    "initial_password_reminder_pending": False,
                    "research_consent_required": True,
                    "research_consent_version": "2026-09-14",
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
        self.assertEqual(domain_payload(response)["error"]["code"], "CSRF_FAILED")

    def test_login_authenticates_with_a_csrf_token_and_returns_session_context(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_response = csrf_client.get(reverse("api-csrf"))

        response = csrf_client.post(
            reverse("api-login"),
            data=json.dumps({"username": self.teacher.username, "password": "test-password"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=domain_payload(csrf_response)["csrf_token"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            domain_payload(response)["user"],
            {
                "id": self.teacher.pk,
                "role": User.Role.TEACHER,
                "display_name": "认证教师",
                "username": "auth-teacher",
                "student_number": None,
                "home_url": reverse("courses:list"),
                "uses_initial_password": False,
                "initial_password_reminder_pending": False,
                "research_consent_required": False,
                "research_consent_version": "2026-09-14",
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
            HTTP_X_CSRFTOKEN=domain_payload(csrf_response)["csrf_token"],
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(domain_payload(forbidden)["error"]["code"], "CSRF_FAILED")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(csrf_client.get(reverse("api-session")).status_code, 401)
