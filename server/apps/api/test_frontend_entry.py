import json
import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import resolve, reverse

ROOT = Path(__file__).resolve().parents[3]


class IndependentFrontendEntryTests(SimpleTestCase):
    def test_registered_page_urls_remain_reversible_but_never_render_in_django(self):
        catalog = json.loads((ROOT / 'contracts/frontend-routes.json').read_text())
        for route in catalog['routes']:
            with self.subTest(route=route['name']):
                names = re.findall(r'<int:(\w+)>', route['path'])
                url = '/' + re.sub(r'<int:\w+>', '17', route['path'])
                self.assertEqual(reverse(route['name'], kwargs=dict.fromkeys(names, 17)), url)
                callback = resolve(url).func
                for method in ('get', 'head', 'post'):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn('X-Accel-Redirect', response)
                self.assertEqual(callback.__module__, 'apps.api.frontend_entry')

    def test_unknown_and_retired_mutation_urls_are_not_frontend_entries(self):
        for url in ('/teacher/not-a-page/', '/student/attempts/1/draft/', '/logout/'):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_backend_static_collection_does_not_read_client_source(self):
        from django.conf import settings
        for entry in settings.STATICFILES_DIRS:
            path = Path(entry[1] if isinstance(entry, tuple) else entry)
            self.assertTrue(path.is_relative_to(settings.BASE_DIR))
