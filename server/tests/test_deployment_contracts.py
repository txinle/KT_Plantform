from pathlib import Path
import os
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(TestCase):
    def test_docker_build_context_excludes_secrets_and_generated_artifacts(self):
        dockerignore = (ROOT.parent / ".dockerignore").read_text(encoding="utf-8")

        for entry in (".env", ".git", ".venv", "client/node_modules", "client/dist"):
            with self.subTest(entry=entry):
                self.assertIn(entry, dockerignore.splitlines())

    def test_compose_injects_application_environment_and_supports_host_proxy_port(self):
        compose = (ROOT.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertEqual(compose.count("env_file: ../server/.env"), 6)
        self.assertIn('"${HTTP_PORT:-80}:80"', compose)

    def test_artifact_cleanup_has_a_dedicated_persistent_worker_in_every_runtime(self):
        import yaml
        loader = type("CleanupComposeLoader", (yaml.SafeLoader,), {})
        def tagged(loader, node):
            if isinstance(node, yaml.MappingNode):
                return loader.construct_mapping(node, deep=True)
            return loader.construct_sequence(node, deep=True)
        for tag in ("!override", "!reset"):
            loader.add_constructor(tag, tagged)
        def compose(name):
            return yaml.load((ROOT.parent / "deploy" / name).read_text(encoding="utf-8"), Loader=loader)["services"]
        base = yaml.safe_load((ROOT.parent / "deploy/docker-compose.yml").read_text(encoding="utf-8"))["services"]
        worker = base["artifact-cleanup-worker"]
        self.assertEqual(worker["command"], ["python", "manage.py", "run_research_artifact_cleanups"])
        self.assertIn("media_data:/app/media", worker["volumes"])
        self.assertEqual(worker["env_file"], "../server/.env")
        self.assertEqual(worker["restart"], "unless-stopped")
        school = compose("docker-compose.school.yml")["artifact-cleanup-worker"]
        self.assertEqual(school["environment"]["POSTGRES_POOL_ALIAS"], "kt_background")
        self.assertEqual(school["mem_limit"], "256M")
        self.assertEqual(school["deploy"]["replicas"], 1)
        self.assertIn("media_data:/app/media", school["volumes"])
        test_env = compose("docker-compose.school-test.yml")["artifact-cleanup-worker"]
        self.assertEqual(test_env["env_file"], ["school-test.env"])
        self.assertEqual(test_env["environment"]["POSTGRES_DB"], "kt_e2e")
        bounded = compose("docker-compose.student-concurrency.yml")["artifact-cleanup-worker"]
        self.assertEqual(bounded["cgroup_parent"], "ktconcurrency.slice")
        for name in ("docker-compose.student-entry-sharing.yml", "docker-compose.student-capacity.yml"):
            service = compose(name)["artifact-cleanup-worker"]
            environment = worker["environment"] | school["environment"] | service["environment"]
            self.assertEqual(environment["DATABASE_CONN_MAX_AGE"], 0)
            self.assertIn("SCHOOL_POOL_PROFILE", environment)

    def test_scheduler_and_web_share_persistent_export_storage(self):
        compose = (ROOT.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        web = compose.split("  web:\n", 1)[1].split("\n  scheduler:", 1)[0]
        scheduler = compose.split("  scheduler:\n", 1)[1].split("\n  analytics-worker:", 1)[0]

        self.assertIn("      - media_data:/app/media", web)
        self.assertIn("      - media_data:/app/media", scheduler)

    def test_example_environment_lists_all_production_rollout_controls(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")

        for setting in (
            "CLASS_CENTER_ENABLED=0",
            "STUDENT_LEARNING_ANALYTICS_ENABLED=0",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, example)

    def test_web_worker_count_is_resolved_from_server_resources(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        gunicorn = (ROOT / "config" / "gunicorn.py").read_text(encoding="utf-8")

        self.assertIn('CMD ["gunicorn", "-c", "config/gunicorn.py"', dockerfile)
        self.assertIn("effective_cpu_count", gunicorn)
        self.assertIn("effective_memory_bytes", gunicorn)
        self.assertIn('GUNICORN_WORKERS', gunicorn)
        self.assertNotIn('"--workers", "3"', dockerfile)

    def test_publication_worker_and_transaction_pool_are_separate_services(self):
        compose = (ROOT.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("publication-worker:", compose)
        self.assertIn("run_publication_jobs", compose)
        self.assertIn("pgbouncer:", compose)
        self.assertIn("AUTH_TYPE: scram-sha-256", compose)
        self.assertIn("POOL_MODE: transaction", compose)
        self.assertIn("PUBLICATION_WORKER_CPUS", compose)
        self.assertIn("PUBLICATION_WORKER_MEMORY", compose)

    def test_analytics_worker_has_configurable_resource_limits(self):
        compose = (ROOT.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        analytics_worker = compose.split("  analytics-worker:\n", 1)[1].split(
            "\n  publication-worker:", 1
        )[0]

        self.assertIn("ANALYTICS_WORKER_CPUS", analytics_worker)
        self.assertIn("ANALYTICS_WORKER_MEMORY", analytics_worker)
        self.assertIn("ANALYTICS_WORKER_CPUS=0.75", example)
        self.assertIn("ANALYTICS_WORKER_MEMORY=512M", example)

    @patch.dict(
        os.environ,
        {
            "GUNICORN_WORKERS": "auto",
            "GUNICORN_MEMORY_PER_WORKER_MB": "512",
            "WEB_REPLICAS": "2",
        },
        clear=False,
    )
    def test_auto_workers_share_one_servers_resources_across_replicas(self):
        from config import gunicorn

        with (
            patch.object(gunicorn, "effective_cpu_count", return_value=16),
            patch.object(gunicorn, "effective_memory_bytes", return_value=16 * 1024**3),
        ):
            self.assertEqual(gunicorn.server_worker_count(), 8)

    @patch.dict(
        os.environ,
        {
            "WEB_REPLICAS": "2",
            "DEPLOYMENT_AGGREGATE_VCPU": "",
            "DEPLOYMENT_AGGREGATE_MEMORY_MB": "",
            "POSTGRES_HOST": "pgbouncer",
        },
        clear=False,
    )
    def test_capacity_report_uses_measured_server_resources_when_overrides_are_blank(self):
        from apps.experiments.management.commands import report_300_capacity

        with (
            patch.object(report_300_capacity, "server_worker_count", return_value=8),
            patch.object(report_300_capacity, "effective_cpu_count", return_value=16),
            patch.object(
                report_300_capacity,
                "effective_memory_bytes",
                return_value=16 * 1024**3,
            ),
        ):
            report = report_300_capacity.capacity_report()

        self.assertEqual(report["aggregate_vcpu"], 16)
        self.assertEqual(report["aggregate_memory_bytes"], 16 * 1024**3)
