import importlib.util
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase


class FixedRuntimeTests(SimpleTestCase):
    def module(self):
        path = Path(settings.BASE_DIR) / 'scripts/load/school_resource_budget.py'
        spec = importlib.util.spec_from_file_location('school_budget', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_combined_budget_counts_shared_pg_once_and_preserves_os_margin(self):
        module = self.module()
        gib = 1024**3
        rows = [{'container_id': 'pg', 'scope': 'shared_postgres', 'working_set_bytes': gib, 'memory_max': 'max'},
                {'container_id': 'pg', 'scope': 'shared_postgres', 'working_set_bytes': gib, 'memory_max': 'max'},
                {'container_id': 'web', 'scope': 'isolated_test', 'working_set_bytes': gib, 'memory_max': str(3*gib)},
                {'container_id': 'old', 'scope': 'production_or_other', 'working_set_bytes': gib, 'memory_max': 'max'}]
        result = module.budget(rows, 16*gib, 4*gib)
        self.assertEqual(result['combined_working_set_bytes'], 3*gib)
        self.assertTrue(all(result['checks'].values()))
        self.assertFalse(module.budget(rows, 16*gib, gib)['checks']['host_headroom'])

    def test_measured_candidate_needs_thirty_percent_without_silent_recapping(self):
        module = self.module()
        rows = [{'container_id': 'web', 'scope': 'isolated_test', 'working_set_bytes': 1000, 'memory_max': '1200'}]
        result = module.budget(rows, 16*1024**3, 4*1024**3)
        self.assertFalse(result['checks']['candidate_margin'])
        self.assertEqual(result['recommended_minimum_caps']['web'], 1300)

    def test_fixed_overlay_has12_workers_no_low_capture_quotas_or_dynamic_file(self):
        import yaml
        loader = type('FixedLoader', (yaml.SafeLoader,), {})
        loader.add_constructor('!override', lambda loader, node: loader.construct_sequence(node))
        loader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
        services = yaml.load((Path(settings.REPOSITORY_DIR) / "deploy" / 'docker-compose.school.yml').read_text(), Loader=loader)['services']
        self.assertEqual(services['web']['environment']['GUNICORN_WORKERS'], 12)
        for role in ('web','db','nginx','pgbouncer'):
            self.assertEqual(services[role]['cpus'], 0)
        self.assertEqual(services['web']['environment']['SCHOOL_RUNTIME_PRESSURE_FILE'], '')
        self.assertEqual(services['db']['mem_limit'], 0)

    def test_failure_diagnostic_exposes_categories_without_private_log_content(self):
        path = Path(settings.BASE_DIR)/'scripts/ops/school_failure_diagnostic.py'
        self.assertTrue(path.exists())
        spec = importlib.util.spec_from_file_location('school_failure', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = module.summarize('short-300-http', 1, 'password=private-secret\nTimeoutError\nOperationalError: private SQL', {'errors': {'start:TimeoutError': 3}, 'checks': {'start_success': False}})
        self.assertEqual(value['error_categories'], ['database_error', 'http_timeout'])
        self.assertEqual(value['failed_checks'], ['start_success'])
        self.assertNotIn('private-secret', str(value))
        self.assertNotIn('private SQL', str(value))
        self.assertNotIn('password', str(value))

    def test_short_generator_can_send_synchronized_requests_without_one_cpu_quota(self):
        script=(Path(settings.BASE_DIR)/'scripts/ops/school_candidate_bootstrap.sh').read_text()
        short=next(line for line in script.splitlines() if '--name "$PROJECT-load-short-$count"' in line)
        self.assertNotIn('--cpus 1', short)
        self.assertIn('--cpu-shares 256', short)
