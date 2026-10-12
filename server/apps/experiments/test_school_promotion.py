import hashlib
import importlib.util
import io
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase


class SchoolPromotionTests(SimpleTestCase):
    def test_owned_test_upgrade_refuses_unknown_owner_comment_or_retained_name(self):
        root = Path(settings.BASE_DIR)
        import sys
        sys.path.insert(0, str(root / 'scripts/ops'))
        try:
            spec = importlib.util.spec_from_file_location('cached_reset', root / 'scripts/ops/school_cached_build.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        run = '20261010T115729Z-358195'
        self.assertTrue(module.owned_test_reset_allowed('kt_e2e_user', 'school-cache-candidate '+run, run, False, False))
        self.assertFalse(module.owned_test_reset_allowed('production-user', 'school-cache-candidate '+run, run, False, False))
        self.assertFalse(module.owned_test_reset_allowed('kt_e2e_user', 'unknown', run, False, False))
        self.assertFalse(module.owned_test_reset_allowed('kt_e2e_user', 'school-cache-candidate '+run, run, True, False))
        self.assertFalse(module.owned_test_reset_allowed('kt_e2e_user', 'school-cache-candidate '+run, run, False, True))

    def test_ops_hashing_works_without_python311_file_digest(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input'
            path.write_bytes(b'ops-host-python310')
            with patch.object(hashlib, 'file_digest', None):
                self.assertEqual(module.file_sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_teacher_plan_report_omits_raw_filters_and_identity(self):
        path = Path(settings.BASE_DIR) / 'scripts/ops/school_teacher_probe.py'
        self.assertTrue(path.exists(), 'Teacher baseline probe missing')
        spec = importlib.util.spec_from_file_location('teacher_probe', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        summary = module.summarize_plan({'Plan': {'Node Type': 'Index Scan', 'Plan Rows': 20,
            'Total Cost': 42, 'Index Cond': "username='private-user'", 'Filter': "answer='private-answer'",
            'Plans': [{'Node Type': 'Nested Loop', 'Plan Rows': 1, 'Total Cost': 3}]}})
        self.assertEqual(summary['node_types'], ['Index Scan', 'Nested Loop'])
        self.assertEqual(summary['estimated_rows'], 20)
        self.assertNotIn('private-', str(summary))

    def accepted_profile(self, *, memory_gib=4):
        return {
            "run_id": "accepted-v7", "state": "passed-observed-fixed", "source_sha256": "a"*64,
            "formal_acceptance_passed": True, "pool_aliases_verified": True,
            "shared_postgres_read_only": True, "dynamic_cpu_control": False,
            "observed_kernel_limits": {"memory.max": 'max',
                "memory.swap.max": 'max', "cpu.max": "max 100000", "cpu.weight": "120"},
            "samples": [{"limits_match": True, 'memory_budget_checks': {'host_headroom': True, 'candidate_margin': True, 'combined_working_set_plus_margin': True}}], "applied_at": 10, "ended_at": 4000,
        }

    def test_promotion_pg_resources_follow_source_bound_accepted_profile(self):
        module = self.module()
        resources = module.accepted_postgres_resources(self.accepted_profile(), "a" * 64, "accepted-v7")
        self.assertEqual(resources, {'read_only': True, 'kernel_limits': self.accepted_profile()['observed_kernel_limits']})
        resources = module.accepted_postgres_resources(self.accepted_profile(memory_gib=5), "a" * 64, "accepted-v7")
        self.assertTrue(resources['read_only'])

    def test_promotion_refuses_legacy_three_cpu_and_unbound_five_gib_profiles(self):
        module = self.module()
        old = self.accepted_profile()
        old["observed_kernel_limits"]["cpu.max"] = "300000 100000"
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            module.accepted_postgres_resources(old, "a" * 64, "accepted-v7")
        unbound = self.accepted_profile(memory_gib=5)
        unbound.pop("source_sha256")
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            module.accepted_postgres_resources(unbound, "a" * 64, "accepted-v7")
        wrong_source = self.accepted_profile()
        wrong_source["source_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            module.accepted_postgres_resources(wrong_source, "a" * 64, "accepted-v7")
        wrong_weight = self.accepted_profile()
        wrong_weight['samples'][0]['memory_budget_checks']['candidate_margin'] = False
        with self.assertRaisesRegex(ValueError, 'PostgreSQL'):
            module.accepted_postgres_resources(wrong_weight, 'a' * 64, 'accepted-v7')

    def test_cutover_and_application_rollback_use_same_accepted_pg_resources(self):
        script = (Path(settings.BASE_DIR) / "scripts/ops/school_production_promote.sh").read_text()
        self.assertNotIn("--cpus 3", script)
        self.assertIn("accepted_postgres_resources", script)
        self.assertIn('verify_accepted_postgres_resources', script)
        self.assertNotIn('docker update', script)
        self.assertNotIn('start_owned_pressure_service', script)

    def promotion_evidence(self, report):
        source, frontend = 'a' * 64, 'b' * 64
        config = {'source_sha256': source, 'frontend_sha256': frontend, 'controller_sha256': 'c' * 64,
                  'run_id': 'accepted-v7',
                  'dynamic_cpu_control': False, 'shared_postgres_read_only': True,
                  'scope': 'formal_school_acceptance',
                  'roles': {'web': {'workers': 12}, 'student-read': {'workers': 2},
                            'teacher-read': {'workers': 1}, 'analytics-worker': {'replicas': 1}},
                  'postgres_memory_gib': 4, 'postgres_cpus': 5, 'postgres_cpu_weight': 120,
                  'actual_postgres_settings': {'max_connections': 100}}
        for role in config['roles'].values():
            role.update(actual_running=True, image_id='sha256:' + 'd' * 64, cpus=0, memory_bytes=1024**3, cgroup={'cpu_max':'max 100000'})
        config['shared_postgres_cgroup']={'cpu_max':'max 100000'}
        import json
        config['config_sha256'] = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        evidence = {
            'status.json': {'run_id': 'accepted-v7', 'state': 'passed'},
            'cached-build-provenance.json': {'run_id': 'accepted-v7',
                'candidate_archive_sha256': source, 'frontend_archive_sha256': frontend},
            'postgres-resource-profile.json': self.accepted_profile(),
            'source-config.json': deepcopy(config),
            'baseline.json': {'warmup_started_at': 15, 'started_at': 20, 'ended_at': 1820, 'student_count':300},
            'mixed.json': {'warmup_started_at': 1835, 'started_at': 1840, 'ended_at': 3640, 'student_count':300},
            'baseline-observation.json': {'source': 'docker_events', 'started_at': 15, 'ended_at': 1820,
                'oom_kills': 0, 'swap_start_bytes': 0, 'swap_end_bytes': 0,
                'backlog_samples': [{'counts': {'pending': 0}}],
                'memory_cpu_samples': [{'at': 20}], 'peak_memory_budget': {'checks':{'host_headroom':True,'candidate_margin':True,'combined_working_set_plus_margin':True}}},
            'pressure.json': {'state': 'stopped-restored', 'mode': 'normal', 'full_restoredbool': True,
                'source_sha256': source, 'frontend_sha256': frontend, 'controller_sha256': 'c' * 64,
                'started_at': 15, 'ended_at': 70, 'samples': [{'at': 20}], 'transitions': [],
                'restored': {role: True for role in ('web', 'student-read', 'teacher-read',
                    'analytics-worker', 'student-comparison-worker', 'export-worker')}},
        }
        evidence['observation.json']=deepcopy(evidence['baseline-observation.json'])
        evidence['observation.json'].update(started_at=1835,ended_at=3640)
        for kind in ('sql','auth','visual'):
            evidence[kind+'-verification.json']={'run_id':'accepted-v7','source_sha256':source,'frontend_sha256':frontend,
                'environment':'school_isolated_acceptance', 'checks': {'verified':True}, 'measurements':[{'path':'/checked/', 'status':200}]}
        evidence['sql-verification.json']['measurements']=[{'path':'/checked/', 'status':200,
            'elapsed_seconds':.01, 'query_count':2, 'response_bytes':100, 'plan_summary':{'node_types':['Index Scan']}}]
        evidence['auth-verification.json']['auth_method']='session_cookie'
        evidence['auth-verification.json']['measurements']=[{'path':'/checked/', 'status':200, 'role':role} for role in ('student','teacher')]
        evidence['visual-verification.json']['measurements']=[{'role':role,'viewport':viewport,'zoom':zoom,'screenshot_sha256':'e'*64}
            for role in ('student','teacher') for viewport,zoom in (([1440,900],1),([390,844],1),([1440,900],2))]
        for count in (3, 50, 150, 300):
            for suffix in ('', '-persistence'):
                evidence[f'short-{count}{suffix}.json'] = {
                    'student_count': count, 'passed': True,
                    'checks': {'real_capture': True}, 'source_config': deepcopy(config)}
        import json
        for name, value in evidence.items():
            (report / name).write_text(json.dumps(value))
        return evidence

    def test_promotion_requires_warmup_coverage_and_baseline_runtime_observation(self):
        import json
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            evidence = self.promotion_evidence(report)
            for name, mutation in (
                ('baseline.json', lambda value: value.pop('warmup_started_at')),
                ('mixed.json', lambda value: value.pop('warmup_started_at')),
                ('postgres-resource-profile.json', lambda value: value.update(applied_at=17)),
                ('sql-verification.json', lambda value: value.update(measurements=[])),
                ('auth-verification.json', lambda value: value.update(checks={})),
                ('visual-verification.json', lambda value: value.update(source_sha256='f'*64)),
                ('visual-verification.json', lambda value: value.update(environment='local')),
                ('sql-verification.json', lambda value: value['measurements'][0].pop('query_count')),
                ('auth-verification.json', lambda value: value.update(auth_method='force_login')),
                ('baseline-observation.json', lambda value: value.update(started_at=17)),
                ('baseline-observation.json', lambda value: value.update(ended_at=29)),
                ('baseline-observation.json', lambda value: value.update(memory_cpu_samples=[])),
                ('baseline-observation.json', lambda value: value.update(oom_kills=1)),
                ('baseline-observation.json', lambda value: value.update(swap_end_bytes=1)),
                ('baseline-observation.json', lambda value: value['backlog_samples'][-1]['counts'].update(pending=1)),
            ):
                with self.subTest(name=name, mutation=mutation):
                    value = deepcopy(evidence[name])
                    mutation(value)
                    (report / name).write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')
                    (report / name).write_text(json.dumps(evidence[name]))
            module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')
            (report / 'baseline-observation.json').unlink()
            with self.assertRaises(FileNotFoundError):
                module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')

    def test_promotion_requires_independent_short_persistence_and_full_pressure_restore(self):
        import json
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary)
            evidence = self.promotion_evidence(report)
            accepted = module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')
            self.assertEqual(accepted['frontend_sha256'], 'b' * 64)
            for name, mutation in (
                ('short-150-persistence.json', lambda value: value.update(checks={})),
                ('short-300.json', lambda value: value['source_config'].update(frontend_sha256='d' * 64)),
                ('short-50.json', lambda value: value['source_config']['roles']['web'].update(workers=7)),
                ('source-config.json', lambda value: value.update(dynamic_cpu_control=True)),
                ('postgres-resource-profile.json', lambda value: value.update(samples=[])),
                ('status.json', lambda value: value.update(state='failed')),
            ):
                with self.subTest(name=name, mutation=mutation):
                    value = deepcopy(evidence[name])
                    mutation(value)
                    (report / name).write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')
                    (report / name).write_text(json.dumps(evidence[name]))
            (report / 'cached-build-provenance.json').unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                module.require_promotion_evidence(report, 'a' * 64, 'accepted-v7')

    def test_pressure_unit_is_fixed_root_service_bound_to_one_promotion(self):
        module = self.module()
        run = '20261010T115729Z-358195'
        unit = module.pressure_service_unit(
            Path('/data/kt-platform/school-releases') / run,
            Path('/home/ktdata/school-promotion-private') / run,
            Path('/home/ktdata/school-promotion-reports') / run)
        self.assertIn('WorkingDirectory=/data/kt-platform/KT_Plantform', unit)
        self.assertIn('User=root', unit)
        self.assertIn('--project kt_plantform --production-binding', unit)
        self.assertIn('KillSignal=SIGTERM', unit)
        self.assertIn('Restart=on-failure', unit)
        with self.assertRaisesRegex(ValueError, 'Unknown pressure service path'):
            module.verify_pressure_service('/tmp/kt-school-pressure.service')
        with self.assertRaisesRegex(ValueError, 'promotion'):
            module.pressure_service_unit(Path('/tmp/controller'),
                Path('/home/ktdata/school-promotion-private') / run,
                Path('/home/ktdata/school-promotion-reports') / run)
        script = (Path(settings.BASE_DIR) / 'scripts/ops/school_production_promote.sh').read_text()
        self.assertLess(script.index('stop_owned_pressure_service\n'), script.index('CUTOVER_STARTED=1'))
        self.assertNotIn('start_owned_pressure_service', script)
        self.assertIn('systemctl is-active --quiet kt-school-pressure.service', script)

    def test_promotion_preserves_database_and_rolls_back_routes_only(self):
        path = Path(settings.BASE_DIR) / 'scripts/ops/school_production_promote.sh'
        self.assertTrue(path.exists(), 'Production promotion script missing')
        script = path.read_text()
        self.assertIn('school_release_support.py', script)
        self.assertIn('pg_dump', script)
        self.assertIn('--no-deps', script)
        self.assertIn('--no-build', script)
        self.assertIn('rollback-compose.json', script)
        self.assertNotIn('pg_restore', script)
        self.assertNotIn('down -v', script)
        self.assertNotIn('DROP ', script)
        self.assertNotIn('git reset', script)

    def module(self):
        path = Path(settings.BASE_DIR) / "scripts/ops/school_release_support.py"
        self.assertTrue(path.exists(), "Promotion source verification missing")
        spec = importlib.util.spec_from_file_location("school_release", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def archive(self, directory, files):
        path = directory / "candidate.tar"
        with tarfile.open(path, "w") as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_promotion_binds_tested_source_and_excludes_test_secrets(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "Dockerfile.school").write_bytes(b"FROM verified\n")
            (candidate / 'resume.sh').write_bytes(b'echo verified\n')
            path, digest = self.archive(root, {"Dockerfile.school": b"FROM verified\n", "resume.sh": b'echo verified\r\n', ".env": b"secret", "school-test.env": b"test-secret"})
            release = root / "release"
            module.extract_verified_source(path, digest, candidate, release)
            self.assertFalse((release / ".env").exists())
            self.assertFalse((release / "school-test.env").exists())
            (candidate / "Dockerfile.school").write_bytes(b"FROM changed\n")
            with self.assertRaisesRegex(ValueError, "Tested candidate differs"):
                module.extract_verified_source(path, digest, candidate, root / "changed")

    def test_promotion_rejects_archive_traversal_and_false_acceptance(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "Dockerfile.school").write_bytes(b"FROM verified\n")
            path, digest = self.archive(root, {"Dockerfile.school": b"FROM verified\n", "../outside": b"bad"})
            with self.assertRaisesRegex(ValueError, "Unsafe archive"):
                module.extract_verified_source(path, digest, candidate, root / "release")
        with self.assertRaisesRegex(ValueError, "Formal acceptance"):
            module.require_acceptance({"passed": False}, {"passed": True}, {"state": "passed"})
        with self.assertRaisesRegex(ValueError, "Formal acceptance"):
            module.require_acceptance({"passed": True}, {"passed": True}, {"state": "running"})

    def test_cached_build_has_no_network_install_and_requires_owned_resume(self):
        root = Path(settings.BASE_DIR)
        cached = Path(settings.REPOSITORY_DIR) / "deploy" / 'Dockerfile.school-cached'
        self.assertTrue(cached.exists(), 'Offline cached Dockerfile missing')
        content = cached.read_text()
        self.assertNotIn('pip install', content)
        self.assertNotIn('apt-get', content)
        self.assertNotIn('npm ci', content)
        self.assertNotIn('.school-frontend/spa-assets', content)
        helper = (root / 'scripts/ops/school_cached_build.py').read_text()
        self.assertIn('school-cache-candidate ', helper)
        self.assertIn('kt_e2e_user', helper)
        self.assertIn('DOCKER_BUILDKIT', helper)
        self.assertIn('5.2.16', helper)
        self.assertIn('--pull=false', helper)
        self.assertIn('requirements.txt', helper)
        spec = importlib.util.spec_from_file_location('cached_school', root / 'scripts/ops/school_cached_build.py')
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.path.insert(0, str(root / 'scripts/ops'))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        self.assertEqual(module.safe_failure_message(ValueError('Uploaded archive hash mismatch')), 'Uploaded archive hash mismatch')
        self.assertEqual(module.safe_failure_message(ValueError('secret-password-input')), 'ValueError')
