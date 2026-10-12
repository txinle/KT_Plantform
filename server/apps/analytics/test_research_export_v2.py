from apps.api.test_support import domain_payload
import csv
import io
import json
import tempfile
import zipfile
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, CourseEnrollment, User
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.analytics.research_materialization import PROJECTION_VERSION
from apps.analytics.models import (
    ExportJob,
    ExportSnapshot,
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
)
from apps.assessment.models import ExperimentSession
from apps.experiments.models import Experiment, ExperimentClassSnapshot, ExperimentVersion


class ExperimentExportScopeTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)

    def test_scope_requires_one_experiment_and_explicit_class_mode(self):
        from .export_scope import ExperimentExportScope, ExportScopeValidationError

        with self.assertRaisesMessage(ExportScopeValidationError, "必须选择一个实验"):
            ExperimentExportScope.from_mapping(self.course, {})
        with self.assertRaisesMessage(ExportScopeValidationError, "一次只能导出一个实验"):
            ExperimentExportScope.from_mapping(
                self.course,
                {"experiment_ids": [self.experiment.pk, self.experiment.pk + 1]},
            )
        with self.assertRaisesMessage(ExportScopeValidationError, "必须选择班级范围"):
            ExperimentExportScope.from_mapping(
                self.course, {"experiment_id": self.experiment.pk}
            )

    def test_single_and_all_class_modes_are_unambiguous(self):
        from .export_scope import ExperimentExportScope, ExportScopeValidationError

        single = ExperimentExportScope.from_mapping(
            self.course,
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "single",
                "class_id": self.class_group.pk,
            },
        )
        self.assertEqual(single.serialize()["class_id"], self.class_group.pk)
        self.assertTrue(single.is_complete)

        all_classes = ExperimentExportScope.from_mapping(
            self.course,
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )
        self.assertNotIn("class_id", all_classes.serialize())
        self.assertTrue(all_classes.is_complete)

        with self.assertRaisesMessage(ExportScopeValidationError, "全部班级范围不能指定班级"):
            ExperimentExportScope.from_mapping(
                self.course,
                {
                    "scope_version": 2,
                    "experiment_id": self.experiment.pk,
                    "class_mode": "all",
                    "class_id": self.class_group.pk,
                },
            )

    def test_single_mode_rejects_class_not_published_to_experiment(self):
        from .export_scope import ExperimentExportScope, ExportScopeValidationError

        other_class = ClassGroup.objects.create(
            course=self.course, name="未发布班", academic_year="2026"
        )
        with self.assertRaisesMessage(ExportScopeValidationError, "班级未发布当前实验"):
            ExperimentExportScope.from_mapping(
                self.course,
                {
                    "scope_version": 2,
                    "experiment_id": self.experiment.pk,
                    "class_mode": "single",
                    "class_id": other_class.pk,
                },
            )



class ResearchExportCatalogV2Tests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        session = self.attempt.session
        ResearchSessionMaterialization.objects.create(
            session=session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            session_summary={"session_ref": f"S{session.pk}"},
            session_row=[f"S{session.pk}"],
            row_counts={"responses": 1, "behavior": 2},
        )

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    @patch("apps.analytics.api.views.read_research_bundle")
    def test_catalog_is_lightweight_and_supports_all_published_classes(
        self, read_research_bundle
    ):
        response = self.client.get(
            self.api("exports/"),
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )

        self.assertEqual(response.status_code, 200, response.content[:500])
        read_research_bundle.assert_not_called()
        payload = domain_payload(response)
        self.assertEqual(payload["scope"]["values"]["experiment_id"], self.experiment.pk)
        self.assertEqual(payload["scope"]["values"]["class_mode"], "all")
        self.assertEqual(
            payload["class_choices"],
            [
                {
                    "id": self.class_group.pk,
                    "name": self.class_group.name,
                    "academic_year": self.class_group.academic_year,
                }
            ],
        )
        self.assertEqual(payload["preparation"]["pending_count"], 0)
        totals = {item["key"]: item["total_items"] for item in payload["tables"]}
        self.assertEqual(totals["session_bindings"], 1)
        self.assertEqual(totals["responses"], 1)
        self.assertEqual(totals["behavior_events"], 2)

    def test_catalog_single_class_counts_only_that_class_facts(self):
        other_student = User.objects.create_user(
            username="export-v2-other-student",
            role=User.Role.STUDENT,
            student_number="EXP-V2-2",
        )
        other_class = ClassGroup.objects.create(
            course=self.course, name="已发布二班", academic_year="2026"
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=other_class, student=other_student
        )
        ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=other_class,
            name_snapshot=other_class.name,
            academic_year_snapshot=other_class.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )

        response = self.client.get(
            self.api("exports/"),
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "single",
                "class_id": self.class_group.pk,
            },
        )

        self.assertEqual(response.status_code, 200, response.content[:500])
        totals = {item["key"]: item["total_items"] for item in domain_payload(response)["tables"]}
        self.assertEqual(totals["session_bindings"], 1)
        self.assertEqual(totals["responses"], 1)
        self.assertEqual(totals["behavior_events"], 2)


class ResearchExportContractV2Tests(TestCase):
    def test_session_binding_contract_contains_class_column(self):
        from .research_contract import FILES

        session_binding = next(item for item in FILES if item["key"] == "session_bindings")
        self.assertEqual(
            session_binding["headers"],
            "课程 学号 姓名 班级 实验序号 实验名称 sessionID".split(),
        )


class ResearchExportAssetTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        session = self.attempt.session
        self.current = ResearchSessionMaterialization.objects.create(
            session=session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            session_summary={
                "session_ref": f"S{session.pk}",
                "student_number": self.student.student_number,
                "name": self.student.shown_name,
                "class_name": self.class_group.name,
                "experiment_id": self.experiment.pk,
                "experiment_name": self.experiment.name,
            },
            session_row=[f"S{session.pk}"],
            row_counts={"responses": 0, "behavior": 0},
        )

    def test_course_asset_is_content_addressed_and_reused(self):
        from .research_export_v2 import get_or_create_course_asset

        first = get_or_create_course_asset(self.course)
        second = get_or_create_course_asset(self.course)

        self.assertEqual(first.pk, second.pk)
        self.assertTrue(first.artifact_path.endswith(".zip"))
        self.assertEqual(first.row_counts["course_students"], 1)
        self.assertEqual(first.row_counts["question_library"], 1)

        extra = User.objects.create_user(
            username="asset-extra-student",
            role=User.Role.STUDENT,
            student_number="ASSET-2",
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=self.class_group, student=extra
        )
        changed = get_or_create_course_asset(self.course)
        self.assertNotEqual(changed.pk, first.pk)
        self.assertEqual(changed.row_counts["course_students"], 2)

        first.content_hash = "0" * 64
        with self.assertRaisesMessage(ValueError, "immutable"):
            first.save()
        with self.assertRaisesMessage(ValueError, "immutable"):
            first.delete()
        with self.assertRaisesMessage(ValueError, "immutable"):
            type(first).objects.filter(pk=first.pk).update(content_hash="1" * 64)
        with self.assertRaisesMessage(ValueError, "immutable"):
            type(first).objects.filter(pk=first.pk).delete()

    def test_experiment_session_asset_excludes_other_experiments_and_has_class(self):
        from .research_export_v2 import get_or_create_experiment_session_asset

        other = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.experiment.question_bank,
            name="其他实验",
        )
        other_version = ExperimentVersion.objects.create(
            experiment=other, version_number=1, published_by=self.teacher
        )
        other_snapshot = ExperimentClassSnapshot.objects.create(
            version=other_version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        other_session = ExperimentSession.objects.create(
            version=other_version,
            student=self.student,
            class_snapshot=other_snapshot,
            class_snapshot_name=other_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        ResearchSessionMaterialization.objects.create(
            session=other_session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            session_summary={
                "session_ref": f"S{other_session.pk}",
                "student_number": self.student.student_number,
                "name": self.student.shown_name,
                "class_name": self.class_group.name,
                "experiment_id": other.pk,
                "experiment_name": other.name,
            },
            session_row=[f"S{other_session.pk}"],
        )

        asset = get_or_create_experiment_session_asset(self.experiment)

        with zipfile.ZipFile(asset.artifact_path) as archive:
            rows = list(
                csv.reader(
                    io.StringIO(
                        archive.read("02_学生Session实验绑定表.csv").decode("utf-8-sig")
                    )
                )
            )
        self.assertEqual(rows[0][3], "班级")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], self.class_snapshot.display_name)
        self.assertEqual(rows[1][-1], f"S{self.attempt.session_id}")
        self.assertNotEqual(f"S{self.attempt.session_id}", f"S{other_session.pk}")


class ResearchExportJobV2Tests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        session = self.attempt.session
        self.materialization = ResearchSessionMaterialization.objects.create(
            session=session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            session_summary={
                "session_ref": f"S{session.pk}",
                "student_number": self.student.student_number,
                "name": self.student.shown_name,
                "class_name": self.class_snapshot.display_name,
                "experiment_id": self.experiment.pk,
                "experiment_name": self.experiment.name,
            },
            session_row=[f"S{session.pk}"],
            row_counts={"responses": 1, "behavior": 1},
        )
        ResearchSessionResponse.objects.create(
            materialization=self.materialization,
            question_ref="Q001",
            ordinal=1,
            row=[f"S{session.pk}", "stale", "stale", "Q001"],
        )
        ResearchSessionBehavior.objects.create(
            materialization=self.materialization,
            question_ref="Q001",
            visit_key="Q001:1",
            ordinal=1,
            row=[f"S{session.pk}", "stale", "stale", "Q001"],
        )

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def payload(self, *, class_mode="single"):
        scope = {
            "scope_version": 2,
            "experiment_id": self.experiment.pk,
            "class_mode": class_mode,
        }
        if class_mode == "single":
            scope["class_id"] = self.class_group.pk
        return {"package": "experiment", "confirm_identified": True, "scope": scope}

    def test_identical_authorized_export_reuses_queued_running_and_valid_completed_job(self):
        from .services import claim_export_job, run_export_job
        created = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        job_id = domain_payload(created)['job_id']
        repeated = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        self.assertEqual(domain_payload(repeated)['job_id'], job_id)
        claimed = claim_export_job(worker_id='dedupe-test')
        running = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        self.assertEqual(domain_payload(running)['job_id'], job_id)
        run_export_job(claimed)
        completed = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        self.assertEqual(domain_payload(completed)['job_id'], job_id)
        self.assertEqual(ExportJob.objects.count(), 1)

    def test_expired_missing_failed_or_changed_source_export_creates_new_job(self):
        import os
        from .services import claim_export_job, run_export_job
        first = domain_payload(self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json'))['job_id']
        ExportJob.objects.filter(pk=first).update(status='failed')
        second = domain_payload(self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json'))['job_id']
        self.assertNotEqual(first, second)
        ready = run_export_job(claim_export_job(worker_id='dedupe-missing'))
        os.unlink(ready.artifact_path)
        third = domain_payload(self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json'))['job_id']
        self.assertNotEqual(second, third)
        from datetime import timedelta
        ExportJob.objects.filter(pk=third).update(expires_at=timezone.now()-timedelta(seconds=1))
        unexpired = domain_payload(self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json'))['job_id']
        self.assertNotEqual(third, unexpired)
        self.course.name += ' revised'
        self.course.save(update_fields=['name'])
        changed = domain_payload(self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json'))['job_id']
        self.assertNotEqual(third, changed)

    def test_export_status_excludes_frozen_payload_and_download_rechecks_authorization_and_ttl(self):
        from datetime import timedelta
        from django.test.utils import CaptureQueriesContext
        from .services import claim_export_job, run_export_job
        created = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        job = run_export_job(claim_export_job(worker_id='download-test'))
        with CaptureQueriesContext(connection) as queries:
            status = self.client.get(self.api(f'export-jobs/{job.pk}/'))
        self.assertEqual(status.status_code, 200)
        selects = [q['sql'] for q in queries if q['sql'].startswith('SELECT')]
        self.assertFalse(any('"source_bundle"' in sql for sql in selects))
        download = domain_payload(status)['download_url']
        valid = self.client.get(download)
        self.assertEqual(valid.status_code, 200)
        b"".join(valid.streaming_content)
        ExportJob.objects.filter(pk=job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(download).status_code, 404)
        self.assertIsNone(domain_payload(self.client.get(self.api(f'export-jobs/{job.pk}/')))['download_url'])
        ExportJob.objects.filter(pk=job.pk).update(expires_at=None)
        other = User.objects.create_user(username='new-export-owner', role=User.Role.TEACHER)
        self.course.owner = other
        self.course.save(update_fields=['owner'])
        self.assertIn(self.client.get(download).status_code, (403, 404))

    def test_deduplication_rechecks_expiry_after_waiting_for_course_lock(self):
        from .services import create_export_job
        from apps.accounts.course_access import lock_course as real_lock
        response = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        job = ExportJob.objects.select_related('snapshot').get(pk=domain_payload(response)['job_id'])
        def expire_at_lock(course_id):
            ExportJob.objects.filter(pk=job.pk).update(expires_at=timezone.now())
            return real_lock(course_id)
        with patch('apps.accounts.course_access.lock_course', side_effect=expire_at_lock):
            replacement = create_export_job(course=self.course, snapshot=job.snapshot,
                created_by=self.teacher, experiment=self.experiment, package='experiment')
        self.assertNotEqual(replacement.pk, job.pk)

    def test_http_export_freeze_never_compresses_bytes(self):
        with patch('apps.analytics.research_export_v2.zlib.compress', side_effect=AssertionError('HTTP compressed')):
            response = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_create_freezes_lightweight_asset_and_materialization_references(self):
        response = self.client.post(
            self.api("export-jobs/"), self.payload(), content_type="application/json"
        )

        self.assertEqual(response.status_code, 201, response.content[:500])
        job = ExportJob.objects.select_related("snapshot").get()
        frozen = job.snapshot.source_bundle["research_export_v2"]
        self.assertEqual(
            frozen["materialization_ids"], [str(self.materialization.pk)]
        )
        self.assertEqual(
            frozen["materializations"],
            [{"id": str(self.materialization.pk), "source_revision": 0}],
        )
        self.assertIn("course_asset_id", frozen)
        self.assertIn("experiment_session_asset_id", frozen)
        self.assertNotIn("research_files", job.snapshot.source_bundle)
        self.assertNotIn('tables', job.snapshot.source_bundle)
        self.assertNotIn('responses', frozen)
        self.assertNotIn('behavior_events', frozen)
        deferred = frozen['deferred_assets']
        self.assertTrue(all('files_base64' in source for source in deferred.values()))
        self.assertLess(len(json.dumps(job.snapshot.source_bundle)), 100_000)

    def test_http_export_creation_never_writes_asset_archives(self):
        with patch('apps.analytics.research_export_v2._write_asset', side_effect=AssertionError('HTTP wrote ZIP')):
            response = self.client.post(self.api('export-jobs/'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content[:500])

    def test_new_experiment_export_rejects_legacy_scope_contract(self):
        response = self.client.post(
            self.api("export-jobs/"),
            {
                "package": "experiment",
                "confirm_identified": True,
                "scope": {
                    "experiment_id": self.experiment.pk,
                    "version_mode": "single",
                    "version_id": self.version.pk,
                    "class_snapshot_id": self.class_snapshot.pk,
                },
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("scope_version=2", str(domain_payload(response)))

    def test_worker_streams_v2_assets_and_facts_into_one_zip(self):
        from .services import claim_export_job, run_export_job

        created = self.client.post(
            self.api("export-jobs/"),
            self.payload(class_mode="all"),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content[:500])
        claimed = claim_export_job(worker_id="export-v2-test")
        finished = run_export_job(claimed)

        self.assertEqual(finished.status, ExportJob.Status.SUCCEEDED)
        with zipfile.ZipFile(finished.artifact_path) as archive:
            names = set(archive.namelist())
            response_rows = list(
                csv.reader(
                    io.StringIO(
                        archive.read("05_单次实验_逐题作答表.csv").decode("utf-8-sig")
                    )
                )
            )
            behavior_rows = list(
                csv.reader(
                    io.StringIO(
                        archive.read("06_单次实验_行为过程表.csv").decode("utf-8-sig")
                    )
                )
            )
        self.assertEqual(
            names,
            {
                item["path"]
                for item in __import__(
                    "apps.analytics.research_contract", fromlist=["FILES"]
                ).FILES
            },
        )
        self.assertEqual(response_rows[1][1:3], [self.student.student_number, self.student.shown_name])
        self.assertEqual(behavior_rows[1][1:3], [self.student.student_number, self.student.shown_name])

    def test_later_materialization_does_not_change_frozen_export(self):
        from .services import claim_export_job, run_export_job

        created = self.client.post(
            self.api("export-jobs/"), self.payload(), content_type="application/json"
        )
        self.assertEqual(created.status_code, 201, created.content[:500])
        self.materialization.is_current = False
        self.materialization.save(update_fields=["is_current"])
        replacement = ResearchSessionMaterialization.objects.create(
            session=self.attempt.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            source_revision=1,
            session_summary={"session_ref": f"S{self.attempt.session_id}"},
            session_row=[f"S{self.attempt.session_id}"],
        )
        ResearchSessionResponse.objects.create(
            materialization=replacement,
            question_ref="Q999",
            ordinal=1,
            row=[f"S{self.attempt.session_id}", "new", "new", "Q999"],
        )

        finished = run_export_job(claim_export_job(worker_id="frozen-export"))

        with zipfile.ZipFile(finished.artifact_path) as archive:
            rows = list(csv.reader(io.StringIO(archive.read(
                "05_单次实验_逐题作答表.csv"
            ).decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], "Q001")

    def test_create_rejects_scope_with_pending_terminal_session(self):
        pending_student = User.objects.create_user(
            username="pending-export-student",
            role=User.Role.STUDENT,
            student_number="PENDING-1",
        )
        ExperimentSession.objects.create(
            version=self.version,
            student=pending_student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )

        response = self.client.post(
            self.api("export-jobs/"), self.payload(), content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("1 名已结束学生的数据正在后台整理", str(domain_payload(response)))
        self.assertFalse(ExportJob.objects.exists())

    def test_job_detail_get_returns_read_only_status(self):
        created = self.client.post(
            self.api("export-jobs/"), self.payload(), content_type="application/json"
        )
        job_id = domain_payload(created)["job_id"]

        response = self.client.get(self.api(f"export-jobs/{job_id}/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["job_id"], job_id)
        self.assertEqual(domain_payload(response)["status"], "queued")

    def test_all_mode_is_union_while_single_mode_keeps_class_facts_separate(self):
        from .services import claim_export_job, run_export_job

        other_student = User.objects.create_user(
            username="export-union-student",
            role=User.Role.STUDENT,
            student_number="UNION-2",
        )
        other_class = ClassGroup.objects.create(
            course=self.course, name="联合导出二班", academic_year="2026"
        )
        CourseEnrollment.objects.create(
            course=self.course, class_group=other_class, student=other_student
        )
        other_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=other_class,
            name_snapshot=other_class.name,
            academic_year_snapshot=other_class.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        other_session = ExperimentSession.objects.create(
            version=self.version,
            student=other_student,
            class_snapshot=other_snapshot,
            class_snapshot_name=other_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        other_materialization = ResearchSessionMaterialization.objects.create(
            session=other_session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            session_summary={"session_ref": f"S{other_session.pk}"},
            session_row=[f"S{other_session.pk}"],
            row_counts={"responses": 1, "behavior": 1},
        )
        ResearchSessionResponse.objects.create(
            materialization=other_materialization,
            question_ref="Q001",
            ordinal=1,
            row=[f"S{other_session.pk}", "stale", "stale", "Q001"],
        )
        ResearchSessionBehavior.objects.create(
            materialization=other_materialization,
            question_ref="Q001",
            visit_key="Q001:1",
            ordinal=1,
            row=[f"S{other_session.pk}", "stale", "stale", "Q001"],
        )

        all_created = self.client.post(
            self.api("export-jobs/"),
            self.payload(class_mode="all"),
            content_type="application/json",
        )
        self.assertEqual(all_created.status_code, 201, all_created.content[:500])
        all_job = run_export_job(claim_export_job(worker_id="union-all"))
        with zipfile.ZipFile(all_job.artifact_path) as archive:
            all_bindings = list(csv.reader(io.StringIO(archive.read(
                "02_学生Session实验绑定表.csv"
            ).decode("utf-8-sig"))))
            all_responses = list(csv.reader(io.StringIO(archive.read(
                "05_单次实验_逐题作答表.csv"
            ).decode("utf-8-sig"))))

        single_created = self.client.post(
            self.api("export-jobs/"), self.payload(), content_type="application/json"
        )
        self.assertEqual(single_created.status_code, 201, single_created.content[:500])
        single_job = run_export_job(claim_export_job(worker_id="union-single"))
        with zipfile.ZipFile(single_job.artifact_path) as archive:
            single_bindings = list(csv.reader(io.StringIO(archive.read(
                "02_学生Session实验绑定表.csv"
            ).decode("utf-8-sig"))))
            single_responses = list(csv.reader(io.StringIO(archive.read(
                "05_单次实验_逐题作答表.csv"
            ).decode("utf-8-sig"))))

        self.assertEqual(len(all_bindings), 3)
        self.assertEqual(len(single_bindings), 3)
        self.assertEqual(len(all_responses), 3)
        self.assertEqual(len(single_responses), 2)
        all_frozen = all_job.snapshot.source_bundle["research_export_v2"]
        single_frozen = single_job.snapshot.source_bundle["research_export_v2"]
        self.assertEqual(
            all_frozen["course_asset_hash"], single_frozen["course_asset_hash"]
        )
        self.assertEqual(
            all_frozen["experiment_session_asset_hash"],
            single_frozen["experiment_session_asset_hash"],
        )


class ResearchExportSnapshotIsolationTests(TransactionTestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        ResearchSessionMaterialization.objects.create(
            session=self.attempt.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            session_summary={"session_ref": f"S{self.attempt.session_id}"},
            session_row=[f"S{self.attempt.session_id}"],
        )

    def test_snapshot_freezes_references_under_repeatable_read(self):
        from apps.accounts.course_access import lock_course as real_lock_course

        isolation_levels = []

        def capture_isolation(course_id):
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                isolation_levels.append(cursor.fetchone()[0])
            return real_lock_course(course_id)

        with patch(
            "apps.accounts.course_access.lock_course",
            side_effect=capture_isolation,
        ):
            response = self.client.post(
                f"/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/",
                {
                    "package": "experiment",
                    "confirm_identified": True,
                    "scope": {
                        "scope_version": 2,
                        "experiment_id": self.experiment.pk,
                        "class_mode": "single",
                        "class_id": self.class_group.pk,
                    },
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201, response.content[:500])
        self.assertEqual(isolation_levels[0], "repeatable read")

    def test_worker_generation_has_no_database_transaction_and_invalidated_job_cannot_publish(self):
        from .services import ExportContractError, claim_export_job, run_export_job
        created = self.client.post(
            f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            {'package': 'experiment', 'confirm_identified': True, 'scope': {
                'scope_version': 2, 'experiment_id': self.experiment.pk,
                'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(created.status_code, 201)
        job = claim_export_job(worker_id='outside-transaction')
        from .research_export_v2 import materialize_research_export as real_materialize
        def generate(current):
            self.assertFalse(connection.in_atomic_block, 'ZIP generation held database transaction')
            artifact = real_materialize(current)
            ExportJob.objects.filter(pk=current.pk).update(status='invalidated')
            return artifact
        with patch('apps.analytics.research_export_v2.materialize_research_export', side_effect=generate):
            with self.assertRaises(ExportContractError):
                run_export_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'invalidated')

    def test_lost_lease_keeps_another_workers_existing_artifact(self):
        import os
        from pathlib import Path
        from .services import ExportContractError, claim_export_job, run_export_job
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
                'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        job = claim_export_job(worker_id='stale-owner')
        root = Path(self.media.name) / 'exports' / str(job.pk)
        root.mkdir(parents=True, exist_ok=True)
        existing = root / f'experiment-{job.pk}.zip'
        existing.write_bytes(b'other worker published artifact')
        from .research_export_v2 import materialize_research_export as real_materialize
        def generate(current):
            artifact = real_materialize(current)
            ExportJob.objects.filter(pk=current.pk).update(locked_by='new-owner', artifact_path=str(existing))
            return artifact
        with patch('apps.analytics.research_export_v2.materialize_research_export', side_effect=generate):
            with self.assertRaises(ExportContractError):
                run_export_job(job)
        self.assertTrue(existing.exists(), 'stale owner removed another artifact')
        self.assertEqual(existing.read_bytes(), b'other worker published artifact')
        self.assertEqual(list(root.glob('*.zip')), [existing])

    def test_invalidated_worker_cannot_publish_shared_identified_assets(self):
        from .models import ResearchExportAsset
        from .services import ExportContractError, claim_export_job, run_export_job
        from .research_export_v2 import _write_asset as real_write
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
                'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        job = claim_export_job(worker_id='purge-during-assets')
        def invalidate(*args, **kwargs):
            ExportJob.objects.filter(pk=job.pk).update(status='invalidated')
            ExportSnapshot.objects.filter(pk=job.snapshot_id).update(invalidated_at=timezone.now())
            return real_write(*args, **kwargs)
        with patch('apps.analytics.research_export_v2._write_asset', side_effect=invalidate):
            with self.assertRaises(ExportContractError):
                run_export_job(job)
        self.assertEqual(ResearchExportAsset.objects.count(), 0)

    def test_concurrent_workers_compress_a_job_once_and_failure_cannot_reset_new_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from .services import claim_export_job, run_export_job
        from .management.commands.run_export_jobs import Command
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
                'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        claimed = claim_export_job(worker_id='shared-worker')
        barrier = Barrier(2)
        from .research_export_v2 import materialize_research_export as real_materialize
        def run():
            close_old_connections()
            try:
                job = ExportJob.objects.get(pk=claimed.pk)
                barrier.wait(timeout=10)
                return run_export_job(job).artifact_path
            finally:
                connections.close_all()
        with patch('apps.analytics.research_export_v2.materialize_research_export', wraps=real_materialize) as generate:
            with ThreadPoolExecutor(max_workers=2) as executor:
                paths = list(executor.map(lambda _: run(), range(2)))
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(generate.call_count, 1)
        ExportJob.objects.filter(pk=claimed.pk).update(status='running', locked_by='new-owner')
        Command()._handle_job_failure(claimed, RuntimeError('old worker failed'))
        claimed.refresh_from_db()
        self.assertEqual(claimed.locked_by, 'new-owner')
        self.assertEqual(claimed.status, 'running')

    def test_generation_heartbeat_renews_the_90_second_lease(self):
        from threading import Event
        from .services import _export_heartbeat, claim_export_job, renew_export_job_lease as real_renew
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
                'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        job = claim_export_job(worker_id='heartbeat-test')
        previous_expiry = job.lease_expires_at
        renewed = Event()
        def renew(*args, **kwargs):
            result = real_renew(*args, **kwargs)
            renewed.set()
            return result
        with patch('apps.analytics.services.renew_export_job_lease', side_effect=renew):
            with _export_heartbeat(job):
                self.assertTrue(renewed.wait(timeout=25), 'worker heartbeat did not renew its lease')
                self.assertFalse(job._lease_lost.is_set())
        job.refresh_from_db()
        self.assertGreater(job.lease_expires_at, previous_expiry)
        self.assertAlmostEqual((job.lease_expires_at-job.heartbeat_at).total_seconds(), 90, places=1)

    def test_concurrent_identical_requests_create_one_queued_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.test import Client
        from django.db import close_old_connections, connections
        barrier = Barrier(2)
        payload = {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
            'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}
        def create():
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.teacher)
                barrier.wait(timeout=10)
                result = client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/', payload, content_type='application/json')
                self.assertEqual(result.status_code, 201, result.content[:300])
                return domain_payload(result)['job_id']
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ExportJob.objects.count(), 1)

    def test_invalidating_frozen_snapshot_before_job_creation_rejects_export(self):
        from .services import ExportContractError, create_export_job as real_create
        def invalidate(**kwargs):
            ExportSnapshot.objects.filter(pk=kwargs['snapshot'].pk).update(invalidated_at=timezone.now())
            return real_create(**kwargs)
        with patch('apps.analytics.api.views.create_export_job', side_effect=invalidate):
            response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
                {'package': 'experiment', 'confirm_identified': True, 'scope': {'scope_version': 2,
                'experiment_id': self.experiment.pk, 'class_mode': 'single', 'class_id': self.class_group.pk}}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ExportJob.objects.count(), 0)


class ResearchExportHistoryV2Tests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)

    def test_history_queryset_defers_legacy_source_bundle(self):
        from .api.views import _scoped_export_jobs

        snapshot = ExportSnapshot.objects.create(
            course=self.course,
            experiment=self.experiment,
            created_by=self.teacher,
            source_bundle={"legacy": "x" * 100_000},
            filter_definition={
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )
        ExportJob.objects.create(
            course=self.course,
            experiment=self.experiment,
            snapshot=snapshot,
            created_by=self.teacher,
            package="experiment",
        )
        scope = __import__(
            "apps.analytics.export_scope", fromlist=["ExperimentExportScope"]
        ).ExperimentExportScope.from_mapping(
            self.course,
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )

        job = _scoped_export_jobs(self.course, scope).get()

        self.assertIn("source_bundle", job.snapshot.get_deferred_fields())

    def test_history_displays_all_published_classes_without_class_lookup(self):
        snapshot = ExportSnapshot.objects.create(
            course=self.course,
            experiment=self.experiment,
            created_by=self.teacher,
            filter_definition={
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )
        ExportJob.objects.create(
            course=self.course,
            experiment=self.experiment,
            snapshot=snapshot,
            created_by=self.teacher,
            package="experiment",
        )

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/exports/history/",
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "all",
            },
        )

        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertEqual(domain_payload(response)["items"][0]["class_name"], "全部已发布班级")
