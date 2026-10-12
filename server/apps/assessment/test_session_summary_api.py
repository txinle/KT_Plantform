from apps.api.test_support import domain_payload
from django.test import TransactionTestCase

from .test_student_api import StudentApiFixtureMixin


class StudentSessionSummaryTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_summary_preserves_counts_without_creating_attempts_or_exposing_feedback(self):
        session_id = domain_payload(self.start())['session_id']
        response = self.client.get(f'/api/v1/student/sessions/{session_id}/summary/')
        self.assertEqual(response.status_code, 200)
        data = domain_payload(response)
        self.assertEqual(data['session_id'], session_id)
        self.assertEqual(data['submission_count'], 0)
        self.assertEqual(data['attempt_count'], 1)
        self.assertEqual(data['status'], 'in_progress')
        self.assertNotIn('score', data)
        self.assertNotIn('correct_answer', data)

    def test_summary_cannot_be_read_by_another_student(self):
        session_id = domain_payload(self.start())['session_id']
        self.client.force_login(self.peer)
        self.assertEqual(self.client.get(f'/api/v1/student/sessions/{session_id}/summary/').status_code, 404)
