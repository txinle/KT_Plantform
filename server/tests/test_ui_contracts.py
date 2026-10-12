from pathlib import Path
from django.test import SimpleTestCase, override_settings

ROOT = Path(__file__).resolve().parents[1]


class UIContractTests(SimpleTestCase):
    @override_settings(DEBUG=False)
    def test_unknown_backend_route_renders_error_without_product_navigation(self):
        response = self.client.get('/unknown-business-page/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, '无法找到请求的页面', status_code=404)
        self.assertNotContains(response, 'teacher-navigation', status_code=404)

    def test_only_admin_and_error_templates_remain(self):
        self.assertFalse((ROOT / 'templates/base.html').exists())
        self.assertFalse((ROOT / 'templates/components').exists())
        self.assertTrue((ROOT / 'templates/admin/base_site.html').is_file())
