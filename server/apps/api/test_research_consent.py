from apps.api.test_support import domain_payload
import json

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import ResearchConsent, User
from apps.accounts.research_consent import (
    CURRENT_RESEARCH_CONSENT_VERSION,
    RESEARCH_CONSENT_COPY,
)


class ResearchConsentApiTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="consent-student",
            password="test-password",
            role=User.Role.STUDENT,
            student_number="CONSENT-001",
        )
        self.teacher = User.objects.create_user(
            username="consent-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )

    def test_session_marks_an_unaccepted_student_as_requiring_current_consent(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(domain_payload(response)["user"]["research_consent_required"])
        self.assertEqual(
            domain_payload(response)["user"]["research_consent_version"],
            CURRENT_RESEARCH_CONSENT_VERSION,
        )

    def test_session_does_not_require_consent_from_teachers(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(domain_payload(response)["user"]["research_consent_required"])

    def test_get_returns_the_frozen_current_copy_to_a_student(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse("api-research-consent"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            domain_payload(response),
            {"version": CURRENT_RESEARCH_CONSENT_VERSION, **RESEARCH_CONSENT_COPY},
        )
        self.assertNotIn("预计耗时", json.dumps(domain_payload(response), ensure_ascii=False))
        self.assertNotIn("导师XXX", json.dumps(domain_payload(response), ensure_ascii=False))
        self.assertIn(
            "在数据完成不可逆去标识化后，由于无法再次定位个人记录，将不能再单独删除该部分数据。",
            "".join(domain_payload(response)["paragraphs"]),
        )

    def test_teacher_cannot_read_or_accept_student_consent(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("api-research-consent"))

        self.assertEqual(response.status_code, 403)

    def test_post_requires_explicit_acceptance(self):
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("api-research-consent"),
            data=json.dumps(
                {"accepted": False, "version": CURRENT_RESEARCH_CONSENT_VERSION}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ResearchConsent.objects.filter(student=self.student).exists())

    def test_post_rejects_a_stale_version(self):
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("api-research-consent"),
            data=json.dumps({"accepted": True, "version": "superseded-version"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(domain_payload(response)["error"]["code"], "RESEARCH_CONSENT_VERSION_STALE")
        self.assertFalse(ResearchConsent.objects.filter(student=self.student).exists())

    def test_acceptance_is_server_timed_idempotent_and_refreshes_session_state(self):
        self.client.force_login(self.student)
        payload = json.dumps(
            {"accepted": True, "version": CURRENT_RESEARCH_CONSENT_VERSION}
        )

        first = self.client.post(
            reverse("api-research-consent"), payload, content_type="application/json"
        )
        second = self.client.post(
            reverse("api-research-consent"), payload, content_type="application/json"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ResearchConsent.objects.filter(student=self.student).count(), 1)
        consent = ResearchConsent.objects.get(student=self.student)
        self.assertIsNotNone(consent.accepted_at)
        self.assertFalse(
            domain_payload(self.client.get(reverse("api-session")))["user"][
                "research_consent_required"
            ]
        )

    def test_acceptance_is_reused_by_a_separate_authenticated_client(self):
        ResearchConsent.objects.create(
            student=self.student,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        )
        other_client = Client()
        other_client.force_login(self.student)

        response = other_client.get(reverse("api-session"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(domain_payload(response)["user"]["research_consent_required"])


class ResearchConsentEnforcementTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="blocked-student",
            password="test-password",
            role=User.Role.STUDENT,
            student_number="BLOCKED-001",
        )
        self.client.force_login(self.student)

    def assert_consent_required(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(domain_payload(response)["error"]["code"], "RESEARCH_CONSENT_REQUIRED")

    def test_blocks_drf_student_reads_before_view_dispatch(self):
        protected_requests = (
            ("get", "/api/v1/student/courses/"),
            ("post", "/api/v1/student/tasks/999999/start/"),
            ("post", "/api/v1/student/tasks/999999/resume/"),
            ("get", "/api/v1/student/tasks/999999/recovery/"),
            ("get", "/api/v1/student/sessions/999999/questions/999999/"),
            (
                "get",
                "/api/v1/student/sessions/999999/questions/999999/recovery/",
            ),
        )

        for method, path in protected_requests:
            with self.subTest(method=method, path=path):
                self.assert_consent_required(getattr(self.client, method)(path))

    def test_blocks_v1_assessment_writes_before_payload_and_object_lookup(self):
        protected_paths = (
            "/api/v1/student/tasks/999999/start/",
            "/api/v1/student/attempts/999999/draft/",
            "/api/v1/student/attempts/999999/submit/",
            "/api/v1/student/attempts/999999/skip/",
            "/api/v1/student/sessions/999999/finish/",
            "/api/v1/student/sessions/999999/questions/999999/",
        )
        for path in protected_paths:
            with self.subTest(path=path):
                self.assert_consent_required(self.client.post(path, data="not-json", content_type="application/json"))

    def test_retired_assessment_writes_and_page_do_not_dispatch_business_handlers(self):
        paths = (
            "/student/experiments/999999/start/",
            "/student/attempts/999999/draft/",
            "/student/attempts/999999/submit/",
            "/student/attempts/999999/skip/",
            "/student/sessions/999999/finish/",
            "/student/sessions/999999/complete/",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 404)
        response = self.client.get("/student/sessions/999999/questions/999999/")
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'id="kt-app"', status_code=404)
        self.assert_consent_required(self.client.get("/api/v1/student/sessions/999999/questions/999999/"))

    def test_blocks_process_data_writes_before_payload_validation(self):
        for path in (
            "/api/v1/assessment/process/events/batch/",
            "/api/v1/assessment/process/capture-v2/batch/",
            "/api/v1/assessment/process/capture-checkpoints/",
        ):
            with self.subTest(path=path):
                self.assert_consent_required(self.client.post(path, data="not-json", content_type="application/json"))
        self.assertEqual(self.client.post("/assessment/process/events/batch/").status_code, 404)

    def test_acceptance_allows_student_api_dispatch(self):
        ResearchConsent.objects.create(
            student=self.student,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        )

        response = self.client.get("/api/v1/student/courses/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response), {"items": []})


class ResearchConsentCsrfTests(TestCase):
    def test_acceptance_requires_csrf(self):
        student = User.objects.create_user(
            username="csrf-consent-student",
            password="test-password",
            role=User.Role.STUDENT,
            student_number="CSRF-CONSENT-001",
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(student)

        response = client.post(
            reverse("api-research-consent"),
            data=json.dumps(
                {"accepted": True, "version": CURRENT_RESEARCH_CONSENT_VERSION}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(domain_payload(response)["error"]["code"], "CSRF_FAILED")
