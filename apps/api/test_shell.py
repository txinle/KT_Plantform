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




class TeacherExperimentFrontendEntryTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="experiment-entry-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="实验前端入口课程",
            code="EXPERIMENT-ENTRY",
            term="2026 秋",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(
            name="实验前端入口题库",
            course=self.course,
            owner=self.teacher,
        )
        self.experiment = Experiment.objects.create(
            name="实验前端入口实验",
            course=self.course,
            question_bank=self.bank,
            owner=self.teacher,
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        self.client.force_login(self.teacher)

    def test_all_course_experiment_pages_use_the_independent_frontend(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        urls = [
            reverse("courses:experiments", args=[self.course.pk]),
            reverse("courses:experiment_create", args=[self.course.pk]),
            reverse("courses:experiment_detail", args=[self.course.pk, self.experiment.pk]),
            reverse("courses:experiment_edit", args=[self.course.pk, self.experiment.pk]),
            reverse(
                "courses:experiment_publish_plan",
                args=[self.course.pk, self.experiment.pk, plan.pk, 1],
            ),
            reverse(
                "courses:experiment_publish_plan",
                args=[self.course.pk, self.experiment.pk, plan.pk, 2],
            ),
        ]
        with patch.dict("os.environ", {
            "FRONTEND_INTERNAL_REDIRECT": "/__frontend/index.html",
        }):
            responses = [self.client.get(url) for url in urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Accel-Redirect"], "/__frontend/index.html")
            self.assertFalse(response.templates)


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
