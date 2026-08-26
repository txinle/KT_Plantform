import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Course, User
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.analytics.services import create_export_snapshot, build_export_manifest, claim_export_job, run_export_job
from apps.question_bank.models import QuestionBank
from apps.experiments.models import Experiment


class DataCenterContractTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="dc-teacher", password="x", role=User.Role.TEACHER
        )
        self.other = User.objects.create_user(
            username="dc-other", password="x", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            owner=self.teacher, name="数据课程", code="DC101", term="2026春"
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher, course=self.course, name="题库"
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="实验一",
        )

    def test_course_data_uses_research_navigation_tree_and_identity_boundary(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("courses:data", args=[self.course.pk]))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        for text in ("数据中心", "完整去标识化研究包", "原始过程", "身份与数据治理", "不与研究数据包合并"):
            self.assertIn(text, body)
        self.assertNotIn("完整匿名研究包", body)

    def test_snapshot_freezes_cutoff_and_manifest_has_schema_lineage(self):
        before = timezone.now()
        snapshot = create_export_snapshot(
            course=self.course,
            experiment=self.experiment,
            created_by=self.teacher,
        )
        self.assertGreaterEqual(snapshot.cutoff_at, before)
        manifest = build_export_manifest(snapshot, selected_tables=["subjects", "assessment_events"])
        self.assertEqual(manifest["export_snapshot_id"], str(snapshot.pk))
        self.assertEqual(manifest["cutoff_at"], snapshot.cutoff_at.isoformat())
        self.assertIn("event_schema_hashes", manifest)
        self.assertIn("application_git_commit_sha", manifest)

    def test_export_job_is_queued_with_snapshot_and_expires(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("analytics:export_job_create", args=[self.course.pk]),
            {"experiment_id": self.experiment.pk, "package": "process"},
        )
        self.assertEqual(response.status_code, 302)
        job = ExportJob.objects.get()
        self.assertEqual(job.status, ExportJob.Status.QUEUED)
        self.assertEqual(job.snapshot.course_id, self.course.pk)
        self.assertAlmostEqual(
            (job.expires_at - job.created_at).total_seconds(),
            timedelta(days=7).total_seconds(),
            delta=1,
        )

    def test_other_teacher_cannot_create_course_export_job(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("analytics:export_job_create", args=[self.course.pk]),
            {"package": "process"},
        )
        self.assertEqual(response.status_code, 404)

    def test_collection_quality_page_reports_process_metrics(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("courses:quality", args=[self.course.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        for label in ("采集质量", "客户端序号缺口", "时钟未同步比例", "native / legacy projection"):
            self.assertIn(label, body)

    def test_worker_claims_job_with_lease_and_builds_manifest_zip(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, package="process")
        claimed = claim_export_job(worker_id="worker-1")
        self.assertEqual(claimed.pk, job.pk)
        self.assertEqual(claimed.status, ExportJob.Status.RUNNING)
        run_export_job(claimed)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, ExportJob.Status.SUCCEEDED)
        self.assertTrue(claimed.artifact_path)
        import zipfile
        with zipfile.ZipFile(claimed.artifact_path) as archive:
            manifest = json.loads(archive.read("00_manifest/manifest.json"))
        self.assertEqual(manifest["export_snapshot_id"], str(snapshot.pk))
