import json
import os
import zipfile
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Course, User, ResearchStudy, ResearchStudyCourse, ResearchStudyParticipant
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.analytics.services import create_export_snapshot, build_export_manifest, claim_export_job, run_export_job, cleanup_expired_exports, renew_export_job_lease
from apps.analytics.services import execute_purge_request
from apps.accounts.models import DataPurgeRequest
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
        self.assertIn("export_config_hash", manifest)
        self.assertIn("source_cutoff_at", manifest)

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

    def test_generic_research_endpoint_rejects_identity_package(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("analytics:export_job_create", args=[self.course.pk]),
            {"package": "identity", "experiment_id": self.experiment.pk},
        )
        self.assertEqual(response.status_code, 400)

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

    def test_identity_package_is_separate_and_contains_only_identity_mapping(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, kind=ExportJob.Kind.IDENTITY, package="identity")
        run_export_job(claim_export_job(worker_id="identity-worker"))
        job.refresh_from_db()
        with zipfile.ZipFile(job.artifact_path) as archive:
            names = set(archive.namelist())
            self.assertIn("01_identity/identity_mapping.csv", names)
            self.assertNotIn("02_core/attempts.csv", names)

    def test_expired_running_job_can_be_reclaimed(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, status=ExportJob.Status.RUNNING, lease_expires_at=timezone.now() - timedelta(minutes=1))
        reclaimed = claim_export_job(worker_id="reclaimer")
        self.assertEqual(reclaimed.pk, job.pk)
        self.assertEqual(reclaimed.locked_by, "reclaimer")

    def test_worker_failure_requeues_until_retry_limit(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, status=ExportJob.Status.RUNNING, retry_count=1)
        from apps.analytics.management.commands.run_export_jobs import Command
        command = Command()
        command._handle_job_failure(job, RuntimeError("boom"))
        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.Status.QUEUED)
        self.assertEqual(job.retry_count, 2)

    def test_worker_can_renew_only_its_own_lease(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher)
        job = claim_export_job(worker_id="lease-owner")
        before = job.lease_expires_at
        renewed = renew_export_job_lease(job, worker_id="lease-owner")
        self.assertGreater(renewed.lease_expires_at, before)
        self.assertIsNone(renew_export_job_lease(job, worker_id="other"))

    def test_purge_request_executes_and_invalidates_course_exports(self):
        request = DataPurgeRequest.objects.create(
            scope_type="course", scope_definition={"course_id": self.course.pk},
            reason_code="collection_error", requested_by=self.teacher,
            preview_counts={"sessions": 0, "events": 0},
        )
        result = execute_purge_request(request, executed_by=self.teacher)
        request.refresh_from_db()
        self.assertEqual(request.status, DataPurgeRequest.Status.EXECUTED)
        self.assertIn("events", result)
        request.refresh_from_db()
        self.assertEqual(request.result_counts["events"], 0)

    def test_cleanup_expired_exports_marks_job_and_removes_artifact(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, expires_at=timezone.now() - timedelta(minutes=1), status=ExportJob.Status.SUCCEEDED, artifact_path="missing.zip")
        cleanup_expired_exports(now=timezone.now())
        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.Status.EXPIRED)

    def test_package_catalog_produces_distinct_research_files(self):
        expected = {
            "kt": {"02_core/subjects.csv", "06_derived/kt_sequences.csv"},
            "cd": {"01_design_metadata/q_matrix.csv", "06_derived/cd_response_matrix.csv"},
            "process": {"03_raw_events/assessment_events.csv", "03_raw_events/navigation_events.csv"},
            "confidence": {"05_self_report/confidence_assignments.csv", "06_derived/calibration_features.csv"},
            "simulation": {"03_raw_events/simulation_events.csv", "03_raw_events/simulation_snapshots.csv"},
        }
        for package, required_names in expected.items():
            snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
            job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, package=package)
            run_export_job(job)
            job.refresh_from_db()
            with zipfile.ZipFile(job.artifact_path) as archive:
                names = set(archive.namelist())
                self.assertTrue(required_names <= names, (package, names))
                manifest = json.loads(archive.read("00_manifest/manifest.json"))
                self.assertTrue(manifest["row_counts"])

    def test_longitudinal_export_uses_study_subject_id(self):
        study = ResearchStudy.objects.create(name="纵向项目", owner=self.teacher)
        ResearchStudyCourse.objects.create(study=study, course=self.course)
        student = User.objects.create_user(username="longitudinal-student", role=User.Role.STUDENT, student_number="LONG-1")
        ResearchStudyParticipant.objects.create(study=study, student=student)
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("analytics:research_study_export_create", args=[study.pk]), {"package": "longitudinal"})
        self.assertEqual(response.status_code, 302)
        job = ExportJob.objects.get(research_study=study)
        self.assertEqual(job.package, "longitudinal")
        self.assertIsNone(job.experiment_id)
        run_export_job(job)
        job.refresh_from_db()
        with zipfile.ZipFile(job.artifact_path) as archive:
            self.assertIn("study_subject_id", archive.read("02_core/subjects.csv").decode("utf-8"))
