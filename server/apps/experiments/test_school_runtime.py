import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import resolve, reverse

from apps.experiments.management.commands.report_300_capacity import capacity_report


ROOT = Path(settings.BASE_DIR)


class SchoolRuntimeTests(SimpleTestCase):
    def test_school_entrypoint_background_services_have_explicit_process_commands(self):
        import json
        config = (Path(settings.REPOSITORY_DIR) / "deploy" / 'docker-compose.school.yml').read_text()
        expected = {'scheduler': ['python', 'manage.py', 'run_scheduler'],
                    'analytics-worker': ['python', 'manage.py', 'run_research_materializations'],
                    'student-comparison-worker': ['python', 'manage.py', 'run_student_comparisons'],
                    'publication-worker': ['python', 'manage.py', 'run_publication_jobs'],
                    'export-worker': ['docker/export-worker.sh']}
        for role, command in expected.items():
            section = re.split(r'\n  [a-z][a-z-]*:', config.split('\n  ' + role + ':\n', 1)[1], maxsplit=1)[0]
            row = re.search(r'^    command: (\[.*\])$', section, re.M)
            self.assertIsNotNone(row, role + ' loses image CMD when entrypoint is overridden')
            self.assertEqual(json.loads(row[1]), command)

    def test_isolated_http_stack_can_use_cookies_without_changing_secure_defaults(self):
        code = "from config import settings; print(int(settings.SESSION_COOKIE_SECURE), int(settings.CSRF_COOKIE_SECURE))"
        env = {**os.environ, "DJANGO_DEBUG": "0", "DJANGO_SESSION_COOKIE_SECURE": "0", "DJANGO_CSRF_COOKIE_SECURE": "0"}
        self.assertEqual(subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env, text=True).strip(), "0 0")
        env.pop("DJANGO_SESSION_COOKIE_SECURE")
        env.pop("DJANGO_CSRF_COOKIE_SECURE")
        self.assertEqual(subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env, text=True).strip(), "1 1")

    def test_bootstrap_preserves_production_and_requires_new_database(self):
        path = ROOT / "scripts/ops/school_candidate_bootstrap.sh"
        self.assertTrue(path.exists(), "Candidate bootstrap missing")
        script = path.read_text()
        self.assertIn("Unknown or existing kt_e2e database/role; refusing reuse", script)
        self.assertIn("/home/ktdata/school-cache-candidate", script)
        self.assertIn("2097152", script)
        self.assertIn("SELECT count(*) FROM pg_stat_activity", script)
        self.assertNotIn("DROP DATABASE", script)
        self.assertNotIn("down -v", script)
        self.assertIn("nginx -t", script)
        self.assertIn("evaluate_school_load.py", script)

    def test_worker_counts_are_preflight_not_acceptance(self):
        with patch.dict(os.environ, {
            "SCHOOL_RUNTIME_ISOLATION_ENABLED": "1", "GUNICORN_WORKERS": "12",
            "WEB_REPLICAS": "1", "POSTGRES_HOST": "pgbouncer",
        }):
            report = capacity_report()
        self.assertFalse(report["accepted_for_300_users"])
        self.assertEqual(report["role_workers"], {"core": 12, "student_read": 2, "teacher_read": 1})
        self.assertNotIn("at_least_sixteen_web_workers", report["checks"])

    def test_nginx_routes_resolve_and_methods_keep_pool_boundaries(self):
        self.assertTrue((Path(settings.REPOSITORY_DIR) / "deploy" / "nginx/kt-school.conf").exists(), "School routing config missing")
        config = (Path(settings.REPOSITORY_DIR) / "deploy" / "nginx/kt-school.conf").read_text()
        # Evaluate the actual map's regex rows, then verify each business path in Django.
        rows = re.findall(r'~(\^[^\s]+)\s+(auth|assessment|capture|student_read|teacher_read);', config)
        self.assertTrue(rows)
        cases = {
            "/api/v1/student/courses/1/learning/": "student_read",
            "/api/v1/student/courses/1/learning/experiments/2/questions/3/": "student_read",
            "/api/v1/student/tasks/1/results/": "student_read",
            "/api/v1/teacher/courses/1/data/overview/": "teacher_read",
            "/api/v1/teacher/courses/1/data/advanced/": "teacher_read",
            "/api/v1/student/tasks/1/start/": "core",
            "/api/v1/student/sessions/1/questions/2/": "core",
            "/api/v1/student/attempts/1/draft/": "core",
            "/api/v1/teacher/courses/1/data/export-jobs/": "core",
            "/api/v1/teacher/courses/1/classes/": "core",
        }
        for path, expected in cases.items():
            resolve(path)
            for method in ("GET", "POST", "HEAD"):
                target = "core"
                for pattern, pool in rows:
                    if re.search(pattern, method + ":" + path):
                        target = pool
                        break
                post_expected='teacher_read' if path=='/api/v1/teacher/courses/1/data/export-jobs/' else 'core'
                if path in ('/api/v1/student/tasks/1/start/', '/api/v1/student/sessions/1/questions/2/', '/api/v1/student/attempts/1/draft/'):
                    post_expected = 'assessment'
                self.assertEqual(target, expected if method != "POST" else post_expected, (method, path))
        self.assertIn('default "http";', config)
        self.assertIn('https "https";', config)
        self.assertNotIn("proxy_cache", config)

    def test_exact_teacher_status_and_export_create_use_teacher_pool(self):
        config=(Path(settings.REPOSITORY_DIR) / "deploy" / 'nginx/kt-school.conf').read_text()
        rows=re.findall(r'~(\^[^\s]+)\s+(auth|assessment|capture|student_read|teacher_read);',config)
        job='/api/v1/teacher/courses/2/data/export-jobs/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/'
        for method,path,expected in (
            ('GET','/api/v1/teacher/courses/2/data/advanced/status/','teacher_read'),
            ('HEAD','/api/v1/teacher/courses/2/data/advanced/status/','teacher_read'),
            ('POST','/api/v1/teacher/courses/2/data/export-jobs/','teacher_read'),
            ('POST',job,'core'),('DELETE',job,'core'),
            ('POST','/api/v1/teacher/courses/2/classes/','core'),
            ('POST','/api/v1/student/tasks/2/start/','assessment'),
            ('POST','/api/v1/student/sessions/2/questions/3/','assessment'),
            ('POST','/api/v1/teacher/courses/2/data/advanced/status/','core')):
            resolve(path)
            actual=next((pool for pattern,pool in rows if re.search(pattern,method+':'+path)),'core')
            self.assertEqual(actual,expected,(method,path))

    def test_export_artifacts_are_private_and_authorized_download_uses_teacher_pool(self):
        config = (Path(settings.REPOSITORY_DIR) / "deploy" / 'nginx/kt-school.conf').read_text()
        for prefix in ('/media/exports/', '/media/research-assets/'):
            self.assertRegex(config, rf'location\s+\^~\s+{re.escape(prefix)}\s*\{{\s*return\s+404;\s*\}}')
        self.assertRegex(config, r'location\s+/media/\s*\{\s*alias\s+/media/;')
        path = reverse('teacher-export-job-download', kwargs={
            'course_id': 2, 'job_id': 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'})
        self.assertEqual(resolve(path).url_name, 'teacher-export-job-download')
        rows = re.findall(r'~(\^[^\s]+)\s+(student_read|teacher_read);', config)
        for method, expected in (('GET', 'teacher_read'), ('HEAD', 'teacher_read'), ('POST', 'core'), ('DELETE', 'core')):
            pool = next((pool for pattern, pool in rows if re.search(pattern, method + ':' + path)), 'core')
            self.assertEqual(pool, expected, (method, path))
        self.assertIn('COPY server/ /app/', (Path(settings.REPOSITORY_DIR) / "deploy" / 'Dockerfile.school-cached').read_text())

    def test_school_overlay_limits_and_database_aliases(self):
        # Compose's !override tag replaces the externally exposed port list.
        try:
            import yaml
        except ImportError:
            self.skipTest("Optional local YAML parser unavailable; run docker compose config --quiet")
        self.assertTrue((Path(settings.REPOSITORY_DIR) / "deploy" / "docker-compose.school.yml").exists(), "School overlay missing")
        loader = type("ComposeLoader", (yaml.SafeLoader,), {})
        loader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))
        loader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
        services = yaml.load((Path(settings.REPOSITORY_DIR) / "deploy" / "docker-compose.school.yml").read_text(), Loader=loader)["services"]
        for name, workers, alias in (("web", 1, "kt_core"), ("auth", 4, "kt_auth"),
                                     ("assessment", 8, "kt_assessment"), ("capture", 4, "kt_capture"),
                                     ("student-read", 2, "kt_student"), ("teacher-read", 1, "kt_teacher")):
            self.assertEqual(str(services[name]["environment"]["GUNICORN_WORKERS"]), str(workers))
            self.assertEqual(services[name]["environment"]["POSTGRES_POOL_ALIAS"], alias)
            self.assertIn("mem_limit", services[name])
            self.assertNotIn("ports", services[name])
        self.assertEqual(services["nginx"]["ports"], ["127.0.0.1:${SCHOOL_HTTP_PORT:-8080}:80"])
        self.assertEqual(services["scheduler"]["environment"]["POSTGRES_HOST"], "db")
        self.assertEqual(services["analytics-worker"]["deploy"]["replicas"], 1)
        self.assertEqual(services["student-comparison-worker"]["deploy"]["replicas"], 1)
        template = (Path(settings.REPOSITORY_DIR) / "deploy" / "docker/pgbouncer-school.ini.template").read_text()
        for alias, size, reserve in (("kt_core", 1, 0), ("kt_auth", 4, 0), ("kt_assessment", 8, 2),
                                     ("kt_capture", 4, 1), ("kt_student", 2, 0), ("kt_teacher", 1, 0), ("kt_background", 3, 0)):
            self.assertRegex(template, rf'{alias} = .*dbname=\$\{{POSTGRES_DB\}}.*pool_size={size} min_pool_size={size} reserve_pool_size={reserve}')
        self.assertNotIn("kt_password", template)


class MixedLoadAcceptanceTests(SimpleTestCase):
    def load_module(self):
        path = ROOT / "scripts/load/school_mixed_300.py"
        self.assertTrue(path.exists(), "Mixed acceptance harness missing")
        spec = importlib.util.spec_from_file_location("school_load", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_acceptance_requires_real_full_duration_and_oom_observation(self):
        module = self.load_module()
        metrics = {key: {"count": 1, "p95_seconds": value * .5, "p99_seconds": value * .75} for key, value in module.THRESHOLDS.items()}
        metrics.update({key: {"count": 300, "p95_seconds": .5, "p99_seconds": .75} for key in module.CORE_METRICS if key not in metrics})
        metrics['start'] = {'count': 300, 'p95_seconds': 2, 'p99_seconds': 3}
        phase = {"duration_seconds": 1800, "metrics": metrics, "requests": 10000, "errors": 0,
                 "student_count": 300, "teacher_count": 5, "completed_student_count": 300,
                 "scope_switches": 25, "exports_created": 5, "exports_completed": 5,
                 "started_at": 10, "ended_at": 1810}
        phase['capture_protocol'] = {'contract': module.CONTRACT, 'all_events_acknowledged': True,
            'completed_sessions': 300, 'scenarios': {key: 300 for key in (
                'single_choice', 'true_false', 'multiple_choice', 'fill_blank', 'short_answer',
                'ime_protocol_simulation', 'queued_replay_reload_protocol', 'writer_lease_reacquire',
                'required_check', 'hint_observed_protocol', 'authorized_feedback_protocol')}}
        observation = {"oom_kills": 0, "source": "docker_events", "observed_duration_seconds": 1800,
                       "started_at": 10, "ended_at": 1810, "swap_start_bytes": 0, "swap_end_bytes": 0,
                       "teacher_freshness": {"terminal_count": 300, "initial_count": 300, "initial_p95_seconds": 10, "current_count": 300, "latest_count": 300, "latest_p95_seconds": 10},
                       "backlog_samples": [{"counts": {"research": 0, "comparison": 0, "export": 0}}]}
        self.assertTrue(module.evaluate(phase, phase, observation)["passed"])
        for phase_name in ('baseline', 'mixed'):
            for metric, field, value in (('enter_item','p95_seconds',1.01), ('save_draft','p99_seconds',2.01),
                                         ('event_batch','p99_seconds',2.01), ('submit_item','p99_seconds',5.01),
                                         ('finish','p99_seconds',5.01)):
                changed = {**phase, 'metrics': {**metrics, metric: {**metrics[metric], field: value}}}
                phases = (changed,phase) if phase_name=='baseline' else (phase,changed)
                with self.subTest(phase=phase_name, metric=metric, field=field):
                    self.assertFalse(module.evaluate(*phases, observation)['passed'])
        missing_tail = {**phase, 'metrics': {**metrics, 'save_draft': {'count': 1, 'p95_seconds': .5}}}
        self.assertFalse(module.evaluate(missing_tail, phase, observation)['passed'])
        event_change = {**phase, 'metrics': {**metrics, 'event_batch': {**metrics['event_batch'], 'p95_seconds': .605}}}
        self.assertTrue(module.evaluate(phase, event_change, observation)['passed'])
        self.assertEqual(module.THRESHOLDS.get("teacher_search"), 1)
        missing_search = {key: row for key, row in metrics.items() if key != "teacher_search"}
        self.assertFalse(module.evaluate(phase, {**phase, "metrics": missing_search}, observation)["passed"])
        self.assertFalse(module.evaluate(phase, phase, None)["passed"])
        self.assertFalse(module.evaluate(phase, {**phase, "duration_seconds": 30}, observation)["passed"])
        degraded = {key: {**row, "p95_seconds": row["p95_seconds"] * 1.21} for key, row in metrics.items()}
        self.assertFalse(module.evaluate(phase, {**phase, "metrics": degraded}, observation)["passed"])
        self.assertFalse(module.evaluate(phase, phase, {**observation, "swap_end_bytes": 4096})["passed"])
        self.assertFalse(module.evaluate(phase, phase, {**observation, "backlog_samples": [{"counts": {"research": 1}}]})["passed"])
        self.assertFalse(module.evaluate(phase, phase, {**observation, "teacher_freshness": {**observation["teacher_freshness"], "latest_p95_seconds": 31}})["passed"])

    def test_start_gate_requires_all_students_and_both_tail_percentiles(self):
        module = self.load_module()
        good = {'student_count': 300, 'metrics': {'start': {'count': 300, 'p95_seconds': 5, 'p99_seconds': 10}}}
        self.assertTrue(module.start_gate(good))
        for metrics in ({'count': 299, 'p95_seconds': 1, 'p99_seconds': 1},
                        {'count': 300, 'p95_seconds': 5.1, 'p99_seconds': 7},
                        {'count': 300, 'p95_seconds': 2, 'p99_seconds': 10.1},
                        {'count': 300, 'p95_seconds': 1}):
            self.assertFalse(module.start_gate({**good, 'metrics': {'start': metrics}}))
