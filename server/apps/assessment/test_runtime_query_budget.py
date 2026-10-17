import uuid

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import ExperimentSession
from .test_student_api import StudentApiTestCase
from .services import get_or_create_session, record_visit_entry
from .test_support import historical_submission_result


class StudentRuntimeQueryBudgetTests(StudentApiTestCase):
    prepared_paper_fixture = False

    @override_settings(CAPTURE_V2_ENABLED=False, CAPTURE_V2_COURSE_IDS=frozenset(), CAPTURE_V2_USER_IDS=frozenset())
    def test_historical_domain_entry_save_and_submit_avoid_reloading_relations(self):
        self.client.force_login(self.student)
        session = get_or_create_session(self.experiment, self.student, class_task=self.task)
        assignment = session.assignments.get()
        attempt = assignment.attempt
        with CaptureQueriesContext(connection) as entry_queries:
            clicked_at = timezone.now()
            entry = record_visit_entry(session.pk, assignment.pk, client_time=clicked_at,
                navigation_id=uuid.uuid4(), client_monotonic_ms=0, client_time_origin=clicked_at)
        with CaptureQueriesContext(connection) as save_queries:
            saved = self.client.post(
                reverse("assessment_api:save_draft", args=[attempt.pk]),
                data={"answer": "B"}, content_type="application/json",
            )
        with CaptureQueriesContext(connection) as submit_queries:
            submitted = historical_submission_result(student=self.student, attempt_id=attempt.pk,
                document={"submission_token": str(uuid.uuid4())})
        self.assertEqual(entry.attempt.pk, attempt.pk)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(submitted.status_code, 200, submitted.content)
        counts = {
            "entry": len(entry_queries), "save": len(save_queries), "submit": len(submit_queries),
        }
        print(f"Historical domain SQL counts: {counts}")
        # Retain the historical ceilings while exercising retained services directly.
        # Canonical HTTP entry and whole-paper acceptance have separate regressions.
        self.assertLessEqual(counts["entry"], 46, counts)
        self.assertLessEqual(counts["save"], 18, counts)
        self.assertLessEqual(counts["submit"], 101, counts)
