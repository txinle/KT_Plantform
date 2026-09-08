from unittest.mock import patch

from django.test import TestCase, override_settings

from .models import ExperimentSession
from .test_student_api import StudentApiFixtureMixin


@override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
class CapturePageContractTests(StudentApiFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start()
        self.session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assignment = self.session.assignments.get()
        self.url = f'/student/sessions/{self.session.pk}/questions/{self.assignment.pk}/'
        self.env = patch.dict('os.environ', {'VUE_STANDALONE_ENABLED': '1', 'FRONTEND_INTERNAL_REDIRECT': '/__frontend/index.html'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_authorized_capture_page_uses_dedicated_internal_entry(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Capture-Contract'], 'capture-v2')
        self.assertEqual(response['X-Accel-Redirect'], '/__frontend/capture-v2.html')
        self.assertIn('no-store', response['Cache-Control'])

    def test_non_capture_page_and_login_have_no_capture_entry(self):
        ExperimentSession.objects.filter(pk=self.session.pk).update(capture_contract=None)
        response = self.client.get(self.url)
        self.assertEqual(response['X-Accel-Redirect'], '/__frontend/index.html')
        self.assertNotIn('X-Capture-Contract', response)
        self.assertNotIn('X-Capture-Contract', self.client.get('/login/'))

    def test_unauthenticated_outsider_and_forced_password_change_cannot_receive_contract(self):
        self.client.logout()
        anonymous = self.client.get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertNotIn('X-Capture-Contract', anonymous)
        self.client.force_login(self.outsider)
        outsider = self.client.get(self.url)
        self.assertEqual(outsider.status_code, 404)
        self.assertNotIn('X-Capture-Contract', outsider)
        self.student.must_change_password = True
        self.student.save(update_fields=['must_change_password'])
        self.client.force_login(self.student)
        change = self.client.get(self.url)
        self.assertEqual(change.status_code, 302)
        self.assertNotIn('X-Capture-Contract', change)
