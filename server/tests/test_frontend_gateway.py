import json
import re
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]


def expected_map():
    catalog = json.loads((ROOT / 'contracts/frontend-routes.json').read_text())
    lines = ['# Generated from contracts/frontend-routes.json; keep the contract and this map in sync.', 'map "$request_method:$uri" $frontend_page {', '    default 0;']
    for route in catalog['routes']:
        pattern = re.sub(r'<int:\w+>', '[0-9]+', route['path'])
        lines.append(f'    ~^(GET|HEAD):/{pattern}$ 1;')
    return '\n'.join([*lines, '}', ''])


class FrontendGatewayContractTests(TestCase):
    def test_nginx_whitelist_exactly_matches_the_shared_page_catalog(self):
        target = ROOT / 'deploy/nginx/frontend-pages.conf'
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(), expected_map())

    def test_every_gateway_uses_whitelist_and_preserves_api_proxy(self):
        for name in ('kt-school.conf', 'kt-platform.conf'):
            source = (ROOT / 'deploy/nginx' / name).read_text()
            self.assertIn('include /etc/nginx/frontend-pages.conf;', source)
            self.assertIn('if ($frontend_page) { rewrite ^ /__frontend/index.html last; }', source)
            self.assertIn('location / {', source)
            self.assertIn('proxy_pass http://', source)
            self.assertNotIn('try_files $uri /index.html', source)
        for name in ('docker-compose.yml', 'docker-compose.capture-uat.yml'):
            self.assertIn('./nginx/frontend-pages.conf:/etc/nginx/frontend-pages.conf:ro', (ROOT / 'deploy' / name).read_text())
