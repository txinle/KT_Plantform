from unittest.mock import patch
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Course, User
from apps.api.shell import production_asset_urls
from apps.experiments.models import Experiment
from apps.experiments.publication_services import get_or_create_publish_plan
from apps.question_bank.models import QuestionBank


class VueShellRolloutTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="shell-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="Shell 试点课程",
            code="SHELL-1",
            term="2026-Autumn",
            owner=self.teacher,
        )
        self.client.force_login(self.teacher)
        self.url = reverse("courses:experiments", args=[self.course.pk])
        self.bank = QuestionBank.objects.create(
            name="Shell 题库",
            course=self.course,
            owner=self.teacher,
        )
        self.experiment = Experiment.objects.create(
            name="Shell 实验",
            course=self.course,
            question_bank=self.bank,
            owner=self.teacher,
        )

    def rollout_env(self, **values):
        defaults = {
            "VUE_TEACHER_EXPERIMENTS_ENABLED": "0",
            "VUE_TEACHER_EXPERIMENTS_COURSE_IDS": "",
            "VUE_TEACHER_EXPERIMENTS_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict("os.environ", defaults)

    def test_flag_off_preserves_the_legacy_template(self):
        with self.rollout_env():
            response = self.client.get(self.url)

        self.assertContains(response, 'class="table-shell"')
        self.assertNotContains(response, 'id="kt-app"')

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
    def test_pilot_course_receives_the_vue_shell_on_the_same_url(self):
        with self.rollout_env(
            VUE_TEACHER_EXPERIMENTS_ENABLED="1",
            VUE_TEACHER_EXPERIMENTS_COURSE_IDS=str(self.course.pk),
        ):
            response = self.client.get(self.url)

        self.assertContains(response, 'id="kt-app"')
        self.assertContains(response, 'data-module="teacher-experiments"')
        self.assertContains(response, "http://localhost:5173/@vite/client")
        self.assertNotContains(response, "bootstrap.min.css")

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
    def test_vue_shell_loads_shared_theme_before_vite_assets(self):
        with self.rollout_env(VUE_TEACHER_EXPERIMENTS_ENABLED="1"):
            response = self.client.get(self.url)

        body = response.content.decode()
        self.assertIn("/static/css/kt-theme.css", body)
        self.assertLess(body.index("/static/css/kt-theme.css"), body.index("/@vite/client"))

    def test_non_pilot_course_stays_on_the_legacy_template(self):
        other_course = Course.objects.create(
            name="旧界面课程",
            code="SHELL-2",
            term="2026-Autumn",
            owner=self.teacher,
        )
        with self.rollout_env(
            VUE_TEACHER_EXPERIMENTS_ENABLED="1",
            VUE_TEACHER_EXPERIMENTS_COURSE_IDS=str(self.course.pk),
        ):
            response = self.client.get(
                reverse("courses:experiments", args=[other_course.pk])
            )

        self.assertContains(response, 'class="table-shell"')
        self.assertNotContains(response, 'id="kt-app"')

    def test_unmigrated_course_navigation_remains_a_django_document(self):
        with self.rollout_env(
            VUE_TEACHER_EXPERIMENTS_ENABLED="1",
            VUE_TEACHER_EXPERIMENTS_COURSE_IDS=str(self.course.pk),
        ):
            response = self.client.get(reverse("courses:banks", args=[self.course.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="kt-app"')

    def test_unknown_global_url_is_still_a_django_404(self):
        with self.rollout_env(VUE_TEACHER_EXPERIMENTS_ENABLED="1"):
            response = self.client.get("/not-a-vue-route/")

        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
    def test_all_teacher_experiment_get_routes_share_the_vue_boundary(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        urls = [
            reverse("courses:experiment_create", args=[self.course.pk]),
            reverse(
                "courses:experiment_detail",
                args=[self.course.pk, self.experiment.pk],
            ),
            reverse(
                "courses:experiment_edit",
                args=[self.course.pk, self.experiment.pk],
            ),
            reverse(
                "courses:experiment_publish_plan",
                args=[self.course.pk, self.experiment.pk, plan.pk, 1],
            ),
            reverse(
                "courses:experiment_publish_plan",
                args=[self.course.pk, self.experiment.pk, plan.pk, 2],
            ),
        ]

        with self.rollout_env(VUE_TEACHER_EXPERIMENTS_ENABLED="1"):
            responses = [self.client.get(url) for url in urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="kt-app"')


class VueProductionAssetTests(TestCase):
    def test_manifest_requires_existing_hashed_assets(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "assets").mkdir()
            (root / "assets" / "main-AbC123.js").write_text("", encoding="utf-8")
            (root / "assets" / "main-AbC123.css").write_text("", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "src/main.ts": {
                            "file": "assets/main-AbC123.js",
                            "css": ["assets/main-AbC123.css"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with override_settings(
                VITE_MANIFEST_PATH=manifest,
                VITE_ASSET_ROOT=root,
                STATIC_URL="/static/",
            ):
                assets = production_asset_urls()

        self.assertEqual(
            assets,
            {
                "scripts": ["/static/spa-assets/assets/main-AbC123.js"],
                "styles": ["/static/spa-assets/assets/main-AbC123.css"],
                "vite_client": None,
            },
        )

    def test_manifest_rejects_a_missing_or_unhashed_asset(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({"src/main.ts": {"file": "assets/main.js"}}),
                encoding="utf-8",
            )
            with override_settings(
                VITE_MANIFEST_PATH=manifest,
                VITE_ASSET_ROOT=root,
            ):
                with self.assertRaises(ImproperlyConfigured):
                    production_asset_urls()
