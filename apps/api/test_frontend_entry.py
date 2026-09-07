from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase


class IndependentFrontendEntryTests(SimpleTestCase):
    def test_only_registered_get_pages_are_frontend_entries(self):
        from .frontend_entry import is_frontend_page
        self.assertTrue(is_frontend_page('courses:bank_edit', 'GET'))
        self.assertTrue(is_frontend_page('assessment:complete', 'GET'))
        self.assertFalse(is_frontend_page('courses:bank_update', 'POST'))
        self.assertFalse(is_frontend_page('api-session', 'GET'))
        self.assertFalse(is_frontend_page('admin:index', 'GET'))
        self.assertFalse(is_frontend_page('analytics:export_job_download', 'GET'))

    def test_production_entry_is_an_internal_static_redirect_without_template_rendering(self):
        from .frontend_entry import frontend_response
        with patch.dict('os.environ', {'FRONTEND_INTERNAL_REDIRECT': '/__frontend/index.html'}):
            response = frontend_response()
        self.assertEqual(response['X-Accel-Redirect'], '/__frontend/index.html')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('kt-bootstrap', response.content.decode())

    def test_assessment_entry_keeps_its_authoritative_page_gate(self):
        from .frontend_entry import IndependentFrontendMiddleware
        request = RequestFactory().get('/student/sessions/1/questions/2/')
        request.user = SimpleNamespace(is_authenticated=True, role='student', pk=1)
        request.resolver_match = SimpleNamespace(view_name='assessment:question')
        with patch('apps.api.frontend_entry.is_vue_module_enabled', return_value=True):
            response = IndependentFrontendMiddleware(lambda request: None).process_view(request, lambda request: None, (), {'session_id': 1, 'assignment_id': 2})
        self.assertIsNone(response)
        self.assertTrue(request._kt_standalone)
