from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(TestCase):
    def test_docker_build_context_excludes_secrets_and_generated_artifacts(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        for entry in (".env", ".git", ".venv", "frontend/node_modules", "frontend/dist"):
            with self.subTest(entry=entry):
                self.assertIn(entry, dockerignore.splitlines())

    def test_compose_injects_application_environment_and_supports_host_proxy_port(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertEqual(compose.count("env_file: .env"), 3)
        self.assertIn('"${HTTP_PORT:-80}:80"', compose)

    def test_example_environment_lists_all_production_rollout_controls(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")

        for setting in (
            "CLASS_CENTER_ENABLED=0",
            "STUDENT_LEARNING_ANALYTICS_ENABLED=0",
            "VUE_TEACHER_EXPERIMENTS_ENABLED=0",
            "VUE_TEACHER_QUESTION_BANKS_ENABLED=0",
            "VUE_TEACHER_CLASSES_ENABLED=0",
            "VUE_TEACHER_DATA_CENTER_ENABLED=0",
            "VUE_STUDENT_COURSES_ENABLED=0",
            "VUE_STUDENT_ASSESSMENT_ENABLED=0",
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, example)
