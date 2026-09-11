import base64
import json
import tempfile
import uuid
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature

from apps.accounts.models import ClassGroup, CourseEnrollment, User
from apps.analytics.models import (
    ExportJob,
    ExportSnapshot,
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
    ResearchSessionVisit,
)
from apps.assessment.models import Attempt, ExperimentSession, ResponseSnapshot
from apps.assessment.response_snapshot_services import create_response_snapshot
from apps.assessment.services import get_or_create_session, record_visit
from apps.question_bank.models import Question, QuestionConcept

from .models import ClassExperimentTask, Experiment, ExperimentVersion, PublishPlanClass, QuestionSnapshot
from .publication_services import commit_publish_plan, generate_preview, get_or_create_publish_plan
from . import test_a_plus_publication as publication_fixtures


class ResearchDeletionTests(TestCase):
    def setUp(self):
        publication_fixtures.APlusPreviewAndCommitTests.setUp(self)
        self.client.force_login(self.teacher)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.media.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.tasks = self.publish(self.experiment, [self.class_group, self.class_group_b])
        self.sessions = []
        for task, student in zip(self.tasks, [self.student, self.student_b]):
            session = get_or_create_session(self.experiment, student, class_task=task)
            attempt = record_visit(session.pk, session.assignments.get().pk)
            response = create_response_snapshot(attempt=attempt, content="保留准确答案状态")
            Attempt.objects.filter(pk=attempt.pk).update(current_response_snapshot=response)
            self.sessions.append(session)

    def publish(self, experiment, classes):
        plan = get_or_create_publish_plan(experiment, self.teacher)
        for class_group in classes:
            PublishPlanClass.objects.create(publish_plan=plan, class_group=class_group)
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        tasks = commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        experiment.refresh_from_db()
        return sorted(tasks, key=lambda item: item.class_group_id)

    def url(self, action="experiment", experiment=None, class_group=None):
        base = f"/api/v1/teacher/courses/{self.course.pk}"
        experiment = experiment or self.experiment
        class_group = class_group or self.class_group
        if action == "class":
            return f"/api/v1/teacher/accounts/courses/{self.course.pk}/classes/{class_group.pk}/delete/"
        if action == "remove":
            return f"{base}/classes/{class_group.pk}/experiments/{experiment.pk}/remove/"
        return f"{base}/experiments/{experiment.pk}/deletion/"

    def preview(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()

    def execute(self, url, preview=None):
        preview = preview or self.preview(url)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, json.dumps({
                "confirmation": "delete", "confirmation_token": preview["confirmation_token"],
            }), content_type="application/json")

    def export(self, experiments, *, class_id=None):
        snapshot = ExportSnapshot.objects.create(course=self.course, created_by=self.teacher,
            selected_versions=[{"id": item.versions.first().pk, "version": 1} for item in experiments],
            filter_definition={"experiment_ids": [item.pk for item in experiments], "class_id": class_id},
            source_bundle={"test": "frozen research content"})
        job = ExportJob.objects.create(course=self.course, snapshot=snapshot,
            created_by=self.teacher, status=ExportJob.Status.SUCCEEDED)
        path = Path(self.media.name) / "exports" / str(job.pk) / f"experiment-{job.pk}.zip"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"frozen zip")
        job.artifact_path = str(path)
        job.save(update_fields=["artifact_path"])
        return job, path

    def test_preview_reports_all_classes_sessions_and_export_packages(self):
        self.export([self.experiment])
        data = self.preview(self.url())
        self.assertTrue(data["allowed"])
        self.assertEqual(data["action"], "delete_experiment")
        self.assertEqual(data["counts"]["class_count"], 2)
        self.assertEqual(data["counts"]["session_count"], 2)
        self.assertEqual(data["counts"]["export_count"], 1)
        self.assertEqual({item["id"] for item in data["experiments"][0]["classes"]},
                         {self.class_group.pk, self.class_group_b.pk})

    def test_confirmed_global_delete_removes_formal_tree_and_keeps_course_resources(self):
        job, path = self.export([self.experiment])
        snapshot_id = job.snapshot_id
        materialization = ResearchSessionMaterialization.objects.create(
            session=self.sessions[0]
        )
        ResearchSessionResponse.objects.create(materialization=materialization, ordinal=1)
        ResearchSessionVisit.objects.create(materialization=materialization, ordinal=1)
        ResearchSessionBehavior.objects.create(materialization=materialization, ordinal=1)
        result = self.execute(self.url())
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.json()["deleted"])
        self.assertFalse(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertFalse(ClassExperimentTask.objects.exists())
        self.assertFalse(ExperimentSession.objects.exists())
        self.assertFalse(ResearchSessionMaterialization.objects.exists())
        self.assertFalse(ResponseSnapshot.objects.exists())
        self.assertFalse(QuestionSnapshot.objects.exists())
        self.assertFalse(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertFalse(ExportSnapshot.objects.filter(pk=snapshot_id).exists())
        self.assertFalse(path.exists())
        self.assertEqual(User.objects.filter(pk__in=[self.student.pk, self.student_b.pk]).count(), 2)
        self.assertEqual(ClassGroup.objects.filter(course=self.course).count(), 2)
        self.assertEqual(CourseEnrollment.objects.filter(course=self.course).count(), 2)
        self.assertEqual(Question.objects.filter(bank__course=self.course).count(), 1)
        self.assertEqual(QuestionConcept.objects.count(), 1)

    def test_remove_only_this_class_preserves_other_class_and_shared_release(self):
        shared_job, shared_path = self.export([self.experiment])
        other_job, other_path = self.export([self.experiment], class_id=self.class_group_b.pk)
        remaining_session = self.sessions[1]
        result = self.execute(self.url("remove"))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertFalse(ExperimentSession.objects.filter(pk=self.sessions[0].pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=remaining_session.pk).exists())
        self.assertTrue(ResponseSnapshot.objects.filter(attempt__session=remaining_session).exists())
        self.assertEqual(ClassExperimentTask.objects.filter(experiment=self.experiment).count(), 1)
        self.assertTrue(QuestionSnapshot.objects.filter(version__experiment=self.experiment).exists())
        self.assertFalse(ExportJob.objects.filter(pk=shared_job.pk).exists())
        self.assertFalse(shared_path.exists())
        self.assertTrue(ExportJob.objects.filter(pk=other_job.pk).exists())
        self.assertTrue(other_path.exists())
        class_preview = self.preview(self.url("class"))
        self.assertTrue(class_preview["allowed"], class_preview)

    def test_class_blocks_research_then_deletes_only_memberships_after_removal(self):
        blocked = self.preview(self.url("class"))
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["blocked_reason"], "该班级仍有关联实验及实验数据，请先删除相关实验数据后再删除班级。")
        self.assertEqual(blocked["experiments"][0]["class_session_count"], 1)
        self.assertEqual(self.execute(self.url("class"), blocked).status_code, 409)
        self.assertEqual(ExperimentSession.objects.count(), 2)
        self.assertEqual(self.execute(self.url("remove")).status_code, 200)
        self.assertEqual(self.execute(self.url("class")).status_code, 200)
        self.assertFalse(ClassGroup.objects.filter(pk=self.class_group.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(CourseEnrollment.objects.filter(student=self.student_b).exists())

    def test_missing_confirmation_and_changed_scope_do_not_delete(self):
        url = self.url()
        self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 400)
        preview = self.preview(url)
        self.export([self.experiment])
        self.assertEqual(self.execute(url, preview).status_code, 409)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_unrelated_teacher_cannot_preview_or_delete(self):
        outsider = User.objects.create_user(username="delete-outsider", role=User.Role.TEACHER)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.post(self.url(), {}, content_type="application/json").status_code, 404)

    def test_ordinary_model_delete_still_rejects_immutable_task(self):
        with self.assertRaises(ValidationError):
            self.tasks[0].delete()

    def test_formal_experiment_with_zero_sessions_is_reported_and_can_be_deleted(self):
        from .models import ExperimentSamplingKC
        empty = Experiment.objects.create(course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="已发布但尚未开始",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=1)
        ExperimentSamplingKC.objects.create(experiment=empty,
            concept=self.experiment.sampling_kcs.first().concept)
        self.publish(empty, [self.class_group])
        url = self.url(experiment=empty)
        preview = self.preview(url)
        self.assertEqual(preview["counts"]["session_count"], 0)
        self.assertEqual(preview["counts"]["class_count"], 1)
        self.assertEqual(self.execute(url, preview).status_code, 200)
        self.assertFalse(Experiment.objects.filter(pk=empty.pk).exists())
        self.assertEqual(ExperimentSession.objects.count(), 2)

    def test_audit_failure_rolls_back_database_and_keeps_export_file(self):
        job, path = self.export([self.experiment])
        preview = self.preview(self.url())
        with patch("apps.experiments.research_deletion.log_action", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.execute(self.url(), preview)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertEqual(ExperimentSession.objects.count(), 2)
        self.assertTrue(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(path.exists())

    def test_mixed_export_is_removed_but_other_experiment_and_session_survive(self):
        other = Experiment.objects.create(course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="不得删除的另一实验")
        version = ExperimentVersion.objects.create(experiment=other, version_number=1, published_by=self.teacher)
        session = ExperimentSession.objects.create(version=version, student=self.student)
        mixed, mixed_path = self.export([self.experiment, other])
        kept, kept_path = self.export([other])
        result = self.execute(self.url())
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(mixed_path.exists())
        self.assertFalse(ExportJob.objects.filter(pk=mixed.pk).exists())
        self.assertTrue(kept_path.exists())
        self.assertTrue(ExportJob.objects.filter(pk=kept.pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=session.pk).exists())
        self.assertTrue(Experiment.objects.filter(pk=other.pk).exists())

    def test_frozen_content_identifies_affected_export_even_when_scope_metadata_is_incomplete(self):
        from apps.analytics.research_deletion import affected_export_snapshots
        affected, _ = self.export([self.experiment])
        kept, _ = self.export([self.experiment])
        for job, session in [(affected, self.sessions[0]), (kept, self.sessions[1])]:
            ExportSnapshot.objects.filter(pk=job.snapshot_id).update(
                filter_definition={}, selected_versions=[], source_bundle={"tables": {"sessions":
                    base64.b64encode(f"session_id,experiment_id\n{session.pk},{self.experiment.pk}\n".encode()).decode()}})
        selected = affected_export_snapshots(course_id=self.course.pk,
            experiment_ids=[self.experiment.pk], class_id=self.class_group.pk)
        self.assertEqual(set(selected.values_list("pk", flat=True)), {affected.snapshot_id})

    def test_failed_file_cleanup_is_durable_and_can_be_retried(self):
        from apps.analytics.research_deletion import retry_artifact_cleanups
        job, path = self.export([self.experiment])
        with patch.object(Path, "unlink", side_effect=PermissionError("file is locked")):
            result = self.execute(self.url())
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["cleanup_pending"])
        self.assertFalse(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(path.exists())
        self.assertEqual(retry_artifact_cleanups(course_id=self.course.pk), 1)
        self.assertFalse(path.exists())
        status = self.client.get(f"/api/v1/teacher/courses/{self.course.pk}/deletion-cleanups/{result.json()['cleanup_id']}/")
        self.assertEqual(status.json()["status"], "completed")

    def test_deleted_export_cannot_be_recreated_by_a_stale_worker(self):
        from apps.analytics.services import ExportContractError, run_export_job
        job, path = self.export([self.experiment])
        _ = job.snapshot
        self.assertEqual(self.execute(self.url()).status_code, 200)
        with self.assertRaisesMessage(ExportContractError, "已删除或失效"):
            run_export_job(job)
        self.assertFalse(path.exists())

    def test_draft_with_deleted_bank_remains_editable_and_requires_a_replacement_bank(self):
        draft = Experiment.objects.create(course=self.course, created_by=self.teacher,
                                          question_bank=None, name="等待重新选择题库")
        detail = self.client.get(f"/api/v1/teacher/courses/{self.course.pk}/experiments/{draft.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.json()["question_bank"])
        self.assertTrue(detail.json()["can_manage"])
        from .services import validate_for_publish
        with self.assertRaisesMessage(ValidationError, "请重新选择题库"):
            validate_for_publish(draft)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({"*"}))
    def test_capture_v2_submission_coverage_and_simulation_are_completely_removed(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from apps.assessment import models as runtime
        from apps.assessment import test_capture_v2 as capture_fixtures
        from apps.assessment.capture_v2_services import ingest_capture_batch
        from apps.assessment.services import save_draft, submit_attempt
        from apps.assessment.snapshot_services import create_simulation_snapshot
        from apps.analytics.recording_coverage import generate_scope_coverage
        from apps.analytics.models import RecordingCoverageRun

        third_class = ClassGroup.objects.create(course=self.course, name="过程验证班")
        student = User.objects.create_user(username="delete-capture-v2", student_number="00003", role="student")
        CourseEnrollment.objects.create(course=self.course, class_group=third_class, student=student)
        task = self.publish(self.experiment, [third_class])[0]
        self.session = get_or_create_session(self.experiment, student, class_task=task)
        self.stream = uuid.uuid4()
        attempt = record_visit(self.session.pk, self.session.assignments.get().pk)
        transition = capture_fixtures.CaptureV2Tests.transition(self, attempt, "", "答案")
        ingest_capture_batch(student=student, document={"client_instance_id": str(self.stream), "events": [transition]})
        save_draft(attempt.pk, None, target_event_id=transition["event_id"], expected_version=0)
        submit_attempt(attempt.pk, uuid.uuid4(), target_event_id=transition["event_id"])
        key = default_storage.save("simulation/deletion-test.bin", ContentFile(b"simulation content"))
        create_simulation_snapshot(attempt=attempt, simulation_version="v1", state_metadata={"component_count": 1},
            blob_storage_key=key, content_bytes=b"simulation content")
        generate_scope_coverage([self.session, self.sessions[1]], force=True)
        self.assertTrue(runtime.SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertTrue(runtime.AssessmentEvent.objects.filter(attempt=attempt).exists())
        self.assertTrue(RecordingCoverageRun.objects.exists())
        result = self.execute(self.url())
        self.assertEqual(result.status_code, 200, result.content)
        for model in [runtime.ExperimentSession, runtime.Attempt, runtime.StudentQuestionAssignment,
                      runtime.AssessmentEvent, runtime.ResponseSnapshot, runtime.SubmissionEvent,
                      runtime.GradeRecord, runtime.SimulationSnapshot, runtime.ConfidenceAssignment,
                      runtime.DraftSaveEvent, runtime.QuestionVisitEvent, RecordingCoverageRun]:
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertFalse(default_storage.exists(key))


@skipUnlessDBFeature("has_select_for_update")
class ResearchDeletionConcurrencyTests(TransactionTestCase):
    setUp = ResearchDeletionTests.setUp
    publish = ResearchDeletionTests.publish
    url = ResearchDeletionTests.url
    preview = ResearchDeletionTests.preview
    export = ResearchDeletionTests.export

    def _delete(self, token):
        from .research_deletion import execute_deletion
        request = RequestFactory().post(self.url())
        request.user = self.teacher
        return execute_deletion(request=request, course=self.course, action="delete_experiment",
                                experiment=self.experiment, confirmation_token=token)

    def _start(self, target, errors, done=None):
        def run():
            close_old_connections()
            try:
                target()
            except Exception as exc:
                errors.put(exc)
            finally:
                close_old_connections()
                if done:
                    done.set()
        thread = Thread(target=run)
        thread.start()
        return thread

    def test_export_materialization_finishes_before_confirmed_deletion_cleans_its_file(self):
        from apps.analytics.services import run_export_job
        job, path = self.export([self.experiment])
        token = self.preview(self.url())["confirmation_token"]
        entered, release, deleted = Event(), Event(), Event()
        errors = Queue()

        def materialize(current):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("export release not signalled")
            return current

        with patch("apps.analytics.services._materialize_export_job", side_effect=materialize):
            worker = self._start(lambda: run_export_job(job), errors)
            try:
                self.assertTrue(entered.wait(10))
                deleter = self._start(lambda: self._delete(token), errors, deleted)
                self.assertFalse(deleted.wait(.2), "deletion must wait for the running export")
            finally:
                release.set()
                worker.join(10)
                if "deleter" in locals():
                    deleter.join(10)
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertTrue(deleted.is_set())
        self.assertFalse(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertFalse(path.exists())

    def test_student_write_cannot_create_rows_after_locked_session_is_deleted(self):
        from django.core.exceptions import ObjectDoesNotExist
        from apps.assessment.services import save_draft
        from . import research_deletion
        token = self.preview(self.url())["confirmation_token"]
        attempt_id = Attempt.objects.get(session=self.sessions[0]).pk
        entered, release, write_done = Event(), Event(), Event()
        errors, write_errors = Queue(), Queue()
        original = research_deletion._purge_sessions

        def paused_purge(*args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("delete release not signalled")
            return original(*args)

        with patch("apps.experiments.research_deletion._purge_sessions", side_effect=paused_purge):
            deleter = self._start(lambda: self._delete(token), errors)
            try:
                self.assertTrue(entered.wait(10))
                writer = self._start(lambda: save_draft(attempt_id, "late answer"), write_errors, write_done)
                self.assertFalse(write_done.wait(.2), "student write must wait on the session lock")
            finally:
                release.set()
                deleter.join(10)
                if "writer" in locals():
                    writer.join(10)
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertTrue(write_done.is_set())
        self.assertFalse(write_errors.empty())
        self.assertIsInstance(write_errors.get(), ObjectDoesNotExist)
        self.assertFalse(Attempt.objects.exists())
        self.assertFalse(ResponseSnapshot.objects.exists())

    def test_old_publish_request_cannot_recreate_deleted_experiment(self):
        from . import research_deletion
        plan = self.experiment.publish_plans.get()
        token = self.preview(self.url())["confirmation_token"]
        entered, release, publish_done = Event(), Event(), Event()
        errors, publish_errors = Queue(), Queue()
        original = research_deletion._purge_sessions

        def paused_purge(*args):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("delete release not signalled")
            return original(*args)

        with patch("apps.experiments.research_deletion._purge_sessions", side_effect=paused_purge):
            deleter = self._start(lambda: self._delete(token), errors)
            try:
                self.assertTrue(entered.wait(10))
                publisher = self._start(lambda: commit_publish_plan(plan, self.teacher,
                    expected_revision=plan.plan_revision), publish_errors, publish_done)
                self.assertFalse(publish_done.wait(.2))
            finally:
                release.set()
                deleter.join(10)
                if "publisher" in locals():
                    publisher.join(10)
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertTrue(publish_done.is_set())
        self.assertFalse(publish_errors.empty())
        self.assertFalse(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertFalse(ClassExperimentTask.objects.exists())
