import importlib.util
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


ROOT = Path(settings.BASE_DIR)


class SchoolCapturePriorityTests(SimpleTestCase):
    def module(self):
        path = ROOT / 'scripts/ops/school_capture_priority.py'
        spec = importlib.util.spec_from_file_location('school_pressure_controller', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def sample(self, **changes):
        return {'write_p95': {}, 'core_throttle_ratio': 0, 'pool_waiting': 0,
                'memory_available_bytes': 4 * 1024**3, **changes}

    def test_write_pressure_requires_two_samples_and_borrows_core_cpu(self):
        mod = self.module()
        state = mod.PressureState()
        high = self.sample(write_p95={'save': 1.1})
        self.assertEqual(state.update(high, 0), 'normal')
        self.assertEqual(state.update(high, 5), 'capture_emergency')
        self.assertEqual(mod.cpu_budgets(state.mode)['web'], 12)
        self.assertNotIn('db', mod.cpu_budgets(state.mode))
        self.assertNotIn('scheduler', mod.cpu_budgets(state.mode))

    def test_cpu_throttle_without_wait_or_write_latency_does_not_pause_jobs(self):
        state = self.module().PressureState()
        for at in range(0, 100, 5):
            self.assertEqual(state.update(self.sample(core_throttle_ratio=.95), at), 'normal')

    def test_signal_only_metrics_observe_assessment_and_capture_without_core_noise(self):
        import json
        rows = [json.dumps({'operation': 'submit', 'pool': pool,
                            'request_time': seconds, 'status': 200})
                for pool, seconds in (('core', 60), ('assessment', 1), ('capture', 2))]
        result = self.module().log_metrics(rows, pools=('assessment', 'capture'))
        self.assertEqual(result['write_requests'], 2)
        self.assertEqual(result['write_p95']['submit'], 2)

    def test_signal_only_policy_has_no_dynamic_cpu_budgets(self):
        policy = self.module().signal_only_policy()
        self.assertFalse(policy['dynamic_cpu_control'])
        self.assertEqual(policy['monitored_pools'], ['assessment', 'capture'])
        self.assertNotIn('normal_cpus', policy)
        self.assertNotIn('priority_cpus', policy)

    def pressure_volume(self, root, project='kt-school-e2e'):
        name = project + '_runtime_control'
        return {'Name': name, 'Driver': 'local', 'Scope': 'local',
                'Mountpoint': str(root / 'volumes' / name / '_data'),
                'Labels': {'com.docker.compose.project': project,
                           'com.docker.compose.volume': 'runtime_control'}}

    def test_pressure_volume_uses_the_daemons_actual_data_root(self):
        mod = self.module()
        for name in ('docker-default-root', 'docker-custom-root'):
            root = ROOT / name
            volume = self.pressure_volume(root)
            self.assertEqual(mod.pressure_directory(volume, str(root), 'kt-school-e2e'),
                             Path(volume['Mountpoint']).resolve())

    def test_pressure_volume_rejects_a_mountpoint_outside_the_owned_volume(self):
        mod = self.module()
        root = ROOT / 'docker-custom-root'
        volume = self.pressure_volume(root)
        volume['Mountpoint'] = str(root / 'volumes' / 'other_project_runtime_control' / '_data')
        with self.assertRaises(ValueError):
            mod.pressure_directory(volume, str(root), 'kt-school-e2e')

    def test_pressure_volume_requires_exact_local_project_ownership(self):
        mod = self.module()
        root = ROOT / 'docker-custom-root'
        invalid = ({'Name': 'other_runtime_control'}, {'Driver': 'remote'}, {'Scope': 'global'},
                   {'Labels': {'com.docker.compose.project': 'kt_plantform',
                               'com.docker.compose.volume': 'runtime_control'}},
                   {'Labels': {'com.docker.compose.project': 'kt-school-e2e',
                               'com.docker.compose.volume': 'other'}})
        for changed in invalid:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                mod.pressure_directory({**self.pressure_volume(root), **changed},
                                       str(root), 'kt-school-e2e')

    def test_recovery_holds_and_returns_gradually(self):
        state = self.module().PressureState()
        high = self.sample(write_p95={'start': 4.5})
        state.update(high, 0)
        self.assertEqual(state.update(high, 5), 'capture_priority')
        for at in range(10, 65, 5):
            self.assertEqual(state.update(self.sample(), at), 'capture_priority')
        self.assertEqual(state.update(self.sample(), 65), 'recovering')
        self.assertEqual(state.update(self.sample(), 90), 'recovering')
        self.assertEqual(state.update(self.sample(), 95), 'normal')

    def test_recovery_reenters_emergency_on_new_burst(self):
        state = self.module().PressureState()
        state.mode, state.since = 'recovering', 0
        high = self.sample(write_p95={'events': 2})
        state.update(high, 1)
        self.assertEqual(state.update(high, 6), 'capture_emergency')

    def test_log_parser_only_retains_safe_operation_and_timing(self):
        mod = self.module()
        lines = ['{"operation":"save","request_time":0.7,"status":200}',
                 '{"operation":"events","request_time":0.9,"status":500}',
                 'invalid', '{"operation":"other","request_time":100}']
        metrics = mod.log_metrics(lines)
        self.assertEqual(metrics['write_p95'], {'save': .7, 'events': .9})
        self.assertEqual(metrics['write_errors'], 1)
        self.assertNotIn('other', metrics['write_p95'])

    def test_controller_preserves_actual_nonzero_limits_on_exit(self):
        text = (ROOT / 'scripts/ops/school_capture_priority.py').read_text()
        self.assertIn("original_cpu_max", text)
        self.assertIn("finally:", text)
        self.assertNotIn("docker', 'stop'", text)
        self.assertIn("expires_at", text)
        self.assertIn('deque(maxlen=720)', text)

    def test_nginx_metrics_exclude_identity_and_include_elapsed_proxy_time(self):
        text = (Path(settings.REPOSITORY_DIR) / "deploy" / 'nginx/kt-school.conf').read_text()
        metric_format = text.split('log_format school_pressure', 1)[1].split(';', 1)[0]
        for secret in ('$request_uri', '$args', '$request_body', '$remote_user', '$http_cookie'):
            self.assertNotIn(secret, metric_format)
        self.assertIn('$request_time', metric_format)
        self.assertIn('$upstream_response_time', metric_format)
        self.assertIn('access_log /dev/stdout school_pressure;', text)

    def test_overlay_has_readonly_pressure_and_prioritizes_core(self):
        import yaml
        loader = type('SchoolLoader', (yaml.SafeLoader,), {})
        loader.add_constructor('!override', lambda loader, node: loader.construct_sequence(node))
        loader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
        body = yaml.load((Path(settings.REPOSITORY_DIR) / "deploy" / 'docker-compose.school.yml').read_text(), Loader=loader)
        services = body['services']
        self.assertEqual(services['web']['environment']['GUNICORN_WORKERS'], 1)
        for role, expected in (('assessment', 6), ('startup', 2)):
            configured = str(services[role]['environment']['GUNICORN_WORKERS'])
            default = re.fullmatch(r'\$\{[^:}]+:-(\d+)\}', configured)
            self.assertEqual(int(default[1]) if default else int(configured), expected)
        self.assertEqual(services['student-read']['environment']['GUNICORN_WORKERS'], 2)
        self.assertEqual(services['teacher-read']['environment']['GUNICORN_WORKERS'], 1)
        self.assertEqual(services['analytics-worker']['deploy']['replicas'], 1)
        self.assertEqual(services['web']['cpus'], 0)
        self.assertEqual(services['db']['cpus'], 0)
        self.assertGreater(services['assessment']['cpu_shares'], services['teacher-read']['cpu_shares'])
        for role in ('web', 'auth', 'assessment', 'startup', 'capture', 'student-read', 'teacher-read',
                     'analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker', 'session-submission-worker'):
            self.assertIn('runtime_control:/runtime-control:ro', services[role]['volumes'])
            self.assertEqual(services[role]['environment']['SCHOOL_RUNTIME_PRESSURE_FILE'],
                             '/runtime-control/pressure.json' if role in (
                                 'teacher-read', 'analytics-worker', 'student-comparison-worker',
                                 'publication-worker', 'export-worker', 'session-submission-worker') else '')

    def test_production_binding_requires_accepted_images_and_controller_hash(self):
        mod = self.module()
        binding = {'formal_accepted': True, 'source_sha256': 'a' * 64,
                   'frontend_sha256': 'b' * 64, 'controller_sha256': 'c' * 64,
                   'runtime_images': {'web': 'sha256:' + 'd' * 64}}
        mod.validate_production_binding(binding, {'web': 'sha256:' + 'd' * 64}, 'c' * 64)
        for changed in ({'formal_accepted': False}, {'controller_sha256': 'e' * 64},
                        {'runtime_images': {'web': 'sha256:' + 'e' * 64}}):
            with self.assertRaises(ValueError):
                mod.validate_production_binding({**binding, **changed}, {'web': 'sha256:' + 'd' * 64}, 'c' * 64)

    def test_restart_only_recovers_reviewed_quotas(self):
        mod = self.module()
        normal = {role: f'{round(cpu * 100000)} 100000' for role, cpu in mod.NORMAL_CPUS.items()}
        borrowed = {role: f'{round(cpu * 100000)} 100000' for role, cpu in mod.PRIORITY_CPUS.items()}
        self.assertEqual(mod.recovery_limits(normal), normal)
        self.assertEqual(mod.recovery_limits(borrowed), normal)
        with self.assertRaises(ValueError):
            mod.recovery_limits({**borrowed, 'web': 'max 100000'})
