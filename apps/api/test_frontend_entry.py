from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


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

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL='http://127.0.0.1:5173')
    def test_development_entry_loads_vite_without_a_django_template(self):
        from .frontend_entry import frontend_response

        with patch.dict('os.environ', {'FRONTEND_INTERNAL_REDIRECT': ''}):
            response = frontend_response()

        content = response.content.decode()
        self.assertIn('http://127.0.0.1:5173/@vite/client', content)
        self.assertIn('http://127.0.0.1:5173/src/main.ts', content)
        self.assertNotIn('kt-bootstrap', content)
