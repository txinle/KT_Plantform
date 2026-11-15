from unittest.mock import patch

from django.test import Client, TestCase

from apps.api.test_support import domain_payload
from apps.assessment.models import ExperimentSession
from apps.assessment.test_student_api import StudentApiFixtureMixin


class OfflinePreparationTests(StudentApiFixtureMixin, TestCase):
    issue_url = '/api/v1/auth/offline-preparation/'
    verify_url = issue_url + 'verify/'

    def setUp(self):
        super().setUp()
        self.start()
        session = ExperimentSession.objects.get(student=self.student)
        self.scope = {'session_id': session.pk, 'assignment_id': session.assignments.first().pk}

    def issue(self):
        response = self.client.post(self.issue_url, self.scope, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('no-store', response['Cache-Control'])
        return domain_payload(response)['proof']

    def verify(self, proof):
        return Client().post(self.verify_url, {'proof': proof}, content_type='application/json')

    def test_worker_without_cookies_can_verify_only_the_issued_scope(self):
        proof = self.issue()
        self.assertNotIn(self.client.session.session_key, proof)
        response = self.verify(proof)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response), {'user_id': self.student.pk, **self.scope})

    def test_issue_requires_login_csrf_and_owned_assignment(self):
        self.assertEqual(Client().post(self.issue_url, self.scope, content_type='application/json').status_code, 401)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.student)
        self.assertEqual(protected.post(self.issue_url, self.scope, content_type='application/json').status_code, 403)
        self.client.force_login(self.peer)
        self.assertEqual(self.client.post(self.issue_url, self.scope, content_type='application/json').status_code, 403)

    def test_tampered_and_expired_proofs_are_rejected(self):
        proof = self.issue()
        self.assertEqual(self.verify('invalid' + proof).status_code, 401)
        import time
        with patch('cryptography.fernet.time.time', return_value=time.time() + 61):
            self.assertEqual(self.verify(proof).status_code, 401)

    def test_logout_invalidates_an_unexpired_proof(self):
        proof = self.issue()
        self.client.logout()
        self.assertEqual(self.verify(proof).status_code, 401)

    def test_password_change_invalidates_an_unexpired_proof(self):
        proof = self.issue()
        self.student.set_password('changed-password')
        self.student.save(update_fields=['password'])
        self.assertEqual(self.verify(proof).status_code, 401)
