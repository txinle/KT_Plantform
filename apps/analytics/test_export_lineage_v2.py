import csv
import json
import os
import zipfile
from datetime import timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Course, User, DataPurgeRequest
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.analytics.services import create_export_snapshot, execute_purge_request, run_export_job
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import QuestionBank, Question


class ExportLineageV2Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="lineage-teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="C", code="C", term="T")
        bank = QuestionBank.objects.create(owner=self.teacher, course=self.course, name="B")
        self.experiment = Experiment.objects.create(course=self.course, created_by=self.teacher, question_bank=bank, name="E")
        self.version = ExperimentVersion.objects.create(experiment=self.experiment, version_number=1, published_by=self.teacher)
        q = Question.objects.create(bank=bank, sequence=1, question_type=Question.Type.SHORT, text="Q", correct_answer="A")
        self.qs = QuestionSnapshot.objects.create(version=self.version, source_question=q, source_sequence=1, code="Q1", question_type=Question.Type.SHORT, text="Q", correct_answer="A", primary_kc="KC", all_kcs=["KC"])

    def _session(self, number):
        student = User.objects.create_user(username=number, role=User.Role.STUDENT, student_number=number, display_name=number)
        session = ExperimentSession.objects.create(version=self.version, student=student)
        assignment = StudentQuestionAssignment.objects.create(session=session, snapshot=self.qs, item_instance=get_or_create_default_item_instance(self.qs), assigned_sequence_index=1)
        Attempt.objects.create(session=session, assignment=assignment, status=Attempt.Status.SUBMITTED, final_answer="A", is_correct=True, score=1)
        return session

    def test_snapshot_freezes_event_boundary_fields(self):
        with patch("apps.analytics.services.timezone.now", return_value=timezone.datetime(2026, 1, 1, tzinfo=dt_timezone.utc)):
            snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        self.assertEqual(snapshot.source_cutoff_at, snapshot.cutoff_at)
        self.assertIsNotNone(snapshot.event_high_watermark)
        self.assertTrue(snapshot.schema_manifest_hash)

    def test_export_contains_deterministic_derived_tables_and_provenance(self):
        self._session("S1")
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, package="complete")
        run_export_job(job)
        with zipfile.ZipFile(job.artifact_path) as archive:
            names = set(archive.namelist())
            self.assertIn("00_manifest/derivation_runs.json", names)
            manifest = json.loads(archive.read("00_manifest/manifest.json"))
            self.assertIn("provenance", manifest)
            for table in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features"):
                path = "06_derived/" + table + ".csv"
                rows = list(csv.reader(StringIO(archive.read(path).decode("utf-8-sig"))))
                self.assertEqual(rows[0][0:3], ["student_number", "student_name", "question_code"])
                self.assertNotEqual(manifest["data_status"][table], "placeholder")

    def test_purge_invalidates_snapshot_and_job_with_reason(self):
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher)
        request = DataPurgeRequest.objects.create(scope_type="course", scope_definition={"course_id": self.course.pk}, reason_code="collection_error", requested_by=self.teacher, preview_counts={})
        execute_purge_request(request, executed_by=self.teacher)
        snapshot.refresh_from_db(); job.refresh_from_db()
        self.assertIsNotNone(snapshot.invalidated_at)
        self.assertTrue(snapshot.invalidated_reason)
        self.assertEqual(job.status, ExportJob.Status.INVALIDATED)
