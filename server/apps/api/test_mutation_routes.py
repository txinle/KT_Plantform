from django.test import Client, SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse


class UnifiedMutationRouteTests(SimpleTestCase):
    def test_student_mutations_have_one_api_location(self):
        for name, suffix in [('save_draft', 'draft'), ('submit', 'submit'), ('skip', 'skip')]:
            with self.subTest(name=name):
                url = reverse('assessment_api:' + name, args=[17])
                self.assertEqual(url, f'/api/v1/student/attempts/17/{suffix}/')
                self.assertEqual(resolve(url).url_name, name)
                with self.assertRaises(Resolver404):
                    resolve(f'/student/attempts/17/{suffix}/')
        self.assertEqual(reverse('assessment_api:finish', args=[11]), '/api/v1/student/sessions/11/finish/')

    def test_capture_mutations_and_worker_have_distinct_delivery_paths(self):
        self.assertEqual(reverse('assessment_process:capture_v2_batch'), '/api/v1/assessment/process/capture-v2/batch/')
        with self.assertRaises(Resolver404):
            resolve('/assessment/process/capture-v2/offline-worker.js')
        with self.assertRaises(Resolver404):
            resolve('/assessment/process/capture-v2/batch/')

    def test_anonymous_mutation_returns_json_authentication_error(self):
        response = self.client.post('/api/v1/student/attempts/17/draft/', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')
        self.assertNotIn('Location', response)


class UnifiedMutationAuthorityTests(TestCase):
    def setUp(self):
        from apps.assessment.test_capture_v2 import CaptureV2Tests
        CaptureV2Tests.setUp(self)
        self.client.force_login(self.student)

    def test_finish_confirms_domain_result_without_html_redirect(self):
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], {
            'success': True, 'next_url': reverse('assessment:complete', args=[self.session.pk]),
        })
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.completed_at)

    def test_missing_csrf_rejects_capture_before_writing(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(reverse('assessment_process:capture_v2_batch'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'CSRF_FAILED')
