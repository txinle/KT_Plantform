import uuid

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import ExperimentSession
from .test_student_api import StudentApiTestCase


class StudentRuntimeQueryBudgetTests(StudentApiTestCase):
    @override_settings(CAPTURE_V2_ENABLED=False, CAPTURE_V2_COURSE_IDS=frozenset(), CAPTURE_V2_USER_IDS=frozenset())
    def test_entry_save_and_submit_avoid_reloading_request_relations(self):
        self.start()
        session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        assignment = session.assignments.get()
        attempt = assignment.attempt
        with CaptureQueriesContext(connection) as entry_queries:
            clicked_at = timezone.now()
            entry = self.client.post(
                f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/",
                data={'navigation_id': str(uuid.uuid4()), 'client_occurred_at': clicked_at.isoformat(),
                      'client_monotonic_ms': '0.000', 'client_time_origin': clicked_at.isoformat()},
                content_type='application/json',
            )
        with CaptureQueriesContext(connection) as save_queries:
            saved = self.client.post(
                reverse("assessment_api:save_draft", args=[attempt.pk]),
                data={"answer": "B"}, content_type="application/json",
            )
        with CaptureQueriesContext(connection) as submit_queries:
            submitted = self.client.post(
                reverse("assessment_api:submit", args=[attempt.pk]),
                data={"submission_token": str(uuid.uuid4())}, content_type="application/json",
            )
        self.assertEqual(entry.status_code, 200, entry.content)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(submitted.status_code, 200, submitted.content)
        counts = {
            "entry": len(entry_queries), "save": len(save_queries), "submit": len(submit_queries),
        }
        print(f"Runtime SQL counts: {counts}")
        # Formal POST entry additionally locks the session before loading current
        # content permissions and binds immutable navigation material. Its measured
        # PostgreSQL baseline is 45; save/submit keep their existing budgets.
        self.assertLessEqual(counts["entry"], 45, counts)
        self.assertLessEqual(counts["save"], 18, counts)
        self.assertLessEqual(counts["submit"], 101, counts)
