from datetime import timedelta
import json
import uuid
import zipfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.accounts.research_consent import (
    CURRENT_RESEARCH_CONSENT_VERSION,
    accept_current_research_consent,
)
from apps.analytics.models import ExportJob
from apps.analytics.services import create_export_snapshot, run_export_job
from apps.assessment.services import (
    SubmissionValidationError,
    get_or_create_session,
    record_visit,
    save_draft,
    submit_attempt,
)
from apps.assessment.models import (
    AssessmentEvent,
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.assessment.event_batch_services import ProcessConflictError, append_event_batch
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentQuestionQuota,
    ExperimentSamplingKC,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    PublishOperationJob,
    PublishPlan,
    PublishPlanClass,
    PublishPreviewRun,
    QuestionSnapshot,
    ReleaseSnapshotQuestionMapping,
    ScheduleRevision,
)
from apps.experiments.publication_jobs import claim_publish_operation, run_publish_operation
from apps.experiments.publication_services import (
    PublicationConflict,
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    manual_close_task,
    publish_plan_setup_projection,
    save_plan_selection,
    save_plan_schedules,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.sampling import SamplingValidationError
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)


class PublishPlanModelTests(TestCase):
    def test_publish_plan_exposes_four_step_draft_lifecycle(self):
        self.assertEqual(PublishPlan.Status.DRAFT, "draft")
        self.assertEqual(PublishPlan.Status.PREVIEWED, "previewed")
        self.assertEqual(PublishPlan.Status.COMMITTED, "committed")
        self.assertEqual(PublishPlan.Status.EXPIRED, "expired")


class PublishPlanLifecycleTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="a-plus-publisher",
            role=User.Role.TEACHER,
        )
        self.experiment = Experiment.objects.create(
            name="A+ 发布实验",
            owner=self.teacher,
            created_by=self.teacher,
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
            question_bank=QuestionBank.objects.create(
                name="A+ 发布题库",
                created_by=self.teacher,
            ),
        )

    def test_request_path_expires_stale_plan_before_creating_a_new_one(self):
        stale = PublishPlan.objects.create(
            experiment=self.experiment,
            created_by=self.teacher,
            current_editor=self.teacher,
            last_meaningful_write_at=timezone.now() - timedelta(hours=73),
            expires_at=timezone.now() - timedelta(hours=1),
        )

        plan = get_or_create_publish_plan(self.experiment, self.teacher)

        stale.refresh_from_db()
        self.assertEqual(stale.status, PublishPlan.Status.EXPIRED)
        self.assertNotEqual(plan.pk, stale.pk)
        self.assertEqual(plan.status, PublishPlan.Status.DRAFT)

class APlusPreviewAndCommitTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="a-plus-preview-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="A+ 课程",
            code="APLUS101",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="实验一班",
            academic_year="2026",
        )
        self.student = User.objects.create_user(
            username="A-001",
            student_number="A-001",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.class_group_b = ClassGroup.objects.create(
            course=self.course,
            name="实验二班",
            academic_year="2026",
        )
        self.student_b = User.objects.create_user(
            username="A-002",
            student_number="A-002",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group_b,
            student=self.student_b,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="A+ 题库",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            code="Q01",
            question_type=Question.Type.SHORT,
            text="发布预览题",
            correct_answer="答案",
        )
        concept = Concept.objects.create(course=self.course, code="KC1", name="KC-A")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=bank,
            name="A+ 实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )

    def test_empty_selection_is_a_valid_unchanged_draft(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        original_revision = plan.plan_revision
        original_expiry = plan.expires_at

        saved = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=original_revision,
            class_ids=[],
            exclusions_by_class={},
        )

        self.assertEqual(saved.plan_revision, original_revision)
        self.assertEqual(saved.expires_at, original_expiry)
        self.assertFalse(saved.class_plans.exists())

    def test_identical_schedule_save_does_not_change_revision_or_expiry(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        opens_at = timezone.now() + timedelta(hours=1)
        closes_at = opens_at + timedelta(hours=1)
        save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=opens_at,
            default_closes_at=closes_at,
            overrides={},
        )
        plan.refresh_from_db()
        saved_revision = plan.plan_revision
        saved_expiry = plan.expires_at

        saved = save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=saved_revision,
            default_opens_at=opens_at,
            default_closes_at=closes_at,
            overrides={},
        )

        self.assertEqual(saved.plan_revision, saved_revision)
        self.assertEqual(saved.expires_at, saved_expiry)

    def test_selected_class_defaults_to_an_explicit_open_ended_schedule(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()

        initial = publish_plan_setup_projection(plan)
        self.assertEqual(initial["setup_status"], "complete")
        self.assertEqual(initial["missing_requirements"], [])

        save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=None,
            default_closes_at=None,
            overrides={},
        )
        plan.refresh_from_db()

        complete = publish_plan_setup_projection(plan)
        self.assertEqual(complete["setup_status"], "complete")
        self.assertEqual(complete["missing_requirements"], [])
        self.assertEqual(complete["field_errors"], {})

    def test_preview_accepts_the_default_open_ended_schedule(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()

        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        plan.refresh_from_db()
        self.assertEqual(plan.status, PublishPlan.Status.PREVIEWED)
        self.assertEqual(plan.preview_run_id, preview.pk)
        self.assertEqual(
            list(plan.class_plans.values_list("class_group_id", flat=True)),
            [self.class_group.pk],
        )

    def test_preview_api_enqueues_work_without_running_preview_inline(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        self.client.force_login(self.teacher)

        response = self.client.post(
            f"/api/v1/teacher/publish-plans/{plan.pk}/preview/",
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        self.assertIn("job_id", response.json())
        self.assertIn("status_url", response.json())
        plan.refresh_from_db()
        self.assertIsNone(plan.preview_run_id)

    def test_preview_job_runs_and_exposes_status_to_the_teacher(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        self.client.force_login(self.teacher)
        response = self.client.post(
            f"/api/v1/teacher/publish-plans/{plan.pk}/preview/",
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="preview-job-1",
        )
        job = PublishOperationJob.objects.get(pk=response.json()["job_id"])

        completed = run_publish_operation(job)
        status_response = self.client.get(response.json()["status_url"])

        self.assertEqual(completed.status, PublishOperationJob.Status.SUCCEEDED)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "succeeded")
        self.assertIsNotNone(status_response.json()["preview_run_id"])

    def test_preview_api_reuses_the_same_idempotency_key(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        self.client.force_login(self.teacher)
        url = f"/api/v1/teacher/publish-plans/{plan.pk}/preview/"
        first = self.client.post(
            url,
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-preview-job",
        )
        second = self.client.post(
            url,
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-preview-job",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])

    def test_commit_api_enqueues_then_worker_creates_the_release_atomically(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        self.client.force_login(self.teacher)

        response = self.client.post(
            f"/api/v1/teacher/publish-plans/{plan.pk}/commit/",
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="commit-job-1",
        )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(ClassExperimentTask.objects.exists())
        completed = run_publish_operation(
            PublishOperationJob.objects.get(pk=response.json()["job_id"])
        )
        self.assertEqual(completed.status, PublishOperationJob.Status.SUCCEEDED)
        self.assertEqual(ClassExperimentTask.objects.count(), 1)
        self.assertIsNotNone(completed.result_release_snapshot_id)

    def test_job_becomes_stale_when_plan_revision_changes_before_processing(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        self.client.force_login(self.teacher)
        response = self.client.post(
            f"/api/v1/teacher/publish-plans/{plan.pk}/preview/",
            {"expected_revision": plan.plan_revision},
            content_type="application/json",
        )
        job = PublishOperationJob.objects.get(pk=response.json()["job_id"])
        PublishPlan.objects.filter(pk=plan.pk).update(
            plan_revision=plan.plan_revision + 1
        )

        completed = run_publish_operation(job)

        self.assertEqual(completed.status, PublishOperationJob.Status.STALE)
        self.assertEqual(completed.error_code, "PUBLISH_PLAN_STALE")
        self.assertFalse(PublishPreviewRun.objects.filter(publish_plan=plan).exists())

    def test_expired_worker_lease_is_reclaimed_with_a_retry_count(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        job = PublishOperationJob.objects.create(
            publish_plan=plan,
            operation=PublishOperationJob.Operation.PREVIEW,
            expected_plan_revision=plan.plan_revision,
            requested_by=self.teacher,
            status=PublishOperationJob.Status.RUNNING,
            locked_by="dead-worker",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        reclaimed = claim_publish_operation(worker_id="replacement-worker")

        self.assertEqual(reclaimed.pk, job.pk)
        self.assertEqual(reclaimed.retry_count, 1)
        self.assertEqual(reclaimed.locked_by, "replacement-worker")
        self.assertGreater(reclaimed.lease_expires_at, timezone.now())

    def test_unexpected_worker_failure_retries_three_times_then_fails(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        job = PublishOperationJob.objects.create(
            publish_plan=plan,
            operation=PublishOperationJob.Operation.PREVIEW,
            expected_plan_revision=plan.plan_revision,
            requested_by=self.teacher,
        )

        with patch(
            "apps.experiments.publication_jobs.generate_preview",
            side_effect=RuntimeError("worker crashed"),
        ):
            first = run_publish_operation(job)
            second = run_publish_operation(first)
            third = run_publish_operation(second)

        self.assertEqual(first.status, PublishOperationJob.Status.QUEUED)
        self.assertEqual(second.status, PublishOperationJob.Status.QUEUED)
        self.assertEqual(third.status, PublishOperationJob.Status.FAILED)
        self.assertEqual(third.retry_count, 3)
        self.assertEqual(third.error_code, "WORKER_FAILED")

    def test_business_validation_failure_is_reported_without_retry(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        job = PublishOperationJob.objects.create(
            publish_plan=plan,
            operation=PublishOperationJob.Operation.PREVIEW,
            expected_plan_revision=plan.plan_revision,
            requested_by=self.teacher,
        )

        result = run_publish_operation(job)

        self.assertEqual(result.status, PublishOperationJob.Status.FAILED)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.error_code, "PUBLISH_VALIDATION_FAILED")
        self.assertIn("至少选择一个", result.error_message)

    def test_preview_query_count_is_bounded_for_three_hundred_participants(self):
        students = [
            User(
                username=f"A-PERF-{index:03d}",
                student_number=f"A-PERF-{index:03d}",
                role=User.Role.STUDENT,
            )
            for index in range(2, 301)
        ]
        User.objects.bulk_create(students)
        CourseEnrollment.objects.bulk_create(
            [
                CourseEnrollment(
                    course=self.course,
                    class_group=self.class_group,
                    student=student,
                )
                for student in students
            ]
        )
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()

        with CaptureQueriesContext(connection) as queries:
            generate_preview(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )

        self.assertLess(len(queries), 500)

    def test_commit_query_count_is_bounded_for_three_hundred_participants(self):
        students = [
            User(
                username=f"A-COMMIT-{index:03d}",
                student_number=f"A-COMMIT-{index:03d}",
                role=User.Role.STUDENT,
            )
            for index in range(2, 301)
        ]
        User.objects.bulk_create(students)
        CourseEnrollment.objects.bulk_create(
            [
                CourseEnrollment(
                    course=self.course,
                    class_group=self.class_group,
                    student=student,
                )
                for student in students
            ]
        )
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        plan.refresh_from_db()

        with CaptureQueriesContext(connection) as queries:
            commit_publish_plan(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )

        self.assertLess(len(queries), 400)

    def test_twenty_item_session_start_uses_batched_assignments(self):
        concept = self.experiment.sampling_kcs.get().concept
        for index in range(2, 21):
            question = Question.objects.create(
                bank=self.experiment.question_bank,
                sequence=index,
                code=f"Q{index:02d}",
                question_type=Question.Type.SHORT,
                text=f"发布性能题 {index}",
                correct_answer="答案",
            )
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
        self.experiment.sampling_total_count = 20
        self.experiment.save(update_fields=["sampling_total_count"])
        accept_current_research_consent(
            student=self.student,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        )
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]

        with CaptureQueriesContext(connection) as queries:
            session = get_or_create_session(
                self.experiment,
                self.student,
                class_task=task,
            )

        self.assertEqual(session.attempts.count(), 20)
        self.assertLess(len(queries), 100)

    def test_mixed_review_preview_uses_one_submitted_item_and_one_new_item(self):
        historical_question = self.experiment.question_bank.questions.get(code="Q01")
        concept = self.experiment.sampling_kcs.get().concept
        new_question = Question.objects.create(
            bank=self.experiment.question_bank,
            sequence=2,
            code="Q02",
            question_type=Question.Type.SHORT,
            text="新的发布预览题",
            correct_answer="答案",
        )
        QuestionConcept.objects.create(
            question=new_question,
            concept=concept,
            is_primary=True,
        )
        prior = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=self.experiment.question_bank,
            name="历史实验",
            cross_experiment_item_policy=Experiment.CrossExperimentItemPolicy.UNRESTRICTED,
        )
        prior_version = ExperimentVersion.objects.create(
            experiment=prior,
            version_number=1,
            published_by=self.teacher,
        )
        prior_snapshot = QuestionSnapshot.objects.create(
            version=prior_version,
            source_question=historical_question,
            source_sequence=historical_question.sequence,
            code=historical_question.code,
            question_type=historical_question.question_type,
            text=historical_question.text,
            correct_answer=historical_question.correct_answer,
            primary_kc=concept.name,
        )
        prior_session = ExperimentSession.objects.create(
            version=prior_version,
            student=self.student,
        )
        prior_assignment = StudentQuestionAssignment.objects.create(
            session=prior_session,
            snapshot=prior_snapshot,
            item_instance=get_or_create_default_item_instance(prior_snapshot),
            assigned_sequence_index=1,
        )
        prior_attempt = Attempt.objects.create(
            session=prior_session,
            assignment=prior_assignment,
        )
        now = timezone.now()
        SubmissionEvent.objects.create(
            attempt=prior_attempt,
            submission_token=uuid.uuid4(),
            final_answer="答案",
            server_received_at=now,
            committed_at=now,
        )
        self.experiment.sampling_total_count = 2
        self.experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
        self.experiment.review_count = 1
        self.experiment.save(update_fields=[
            "sampling_total_count",
            "cross_experiment_item_policy",
            "review_count",
        ])

        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()
        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        participant = preview.class_results.get().participants.get()
        roles = list(
            participant.items.order_by("assigned_sequence_index").values_list(
                "sampling_source__history_role", flat=True
            )
        )
        self.assertCountEqual(roles, ["review", "new"])
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]
        session = get_or_create_session(
            self.experiment,
            self.student,
            class_task=task,
        )
        self.assertEqual(session.attempts.count(), 2)
        self.assertFalse(session.attempts.filter(pk=prior_attempt.pk).exists())
        repeated = session.attempts.get(
            assignment__snapshot__source_question=historical_question
        )
        self.assertIsNone(repeated.draft_answer)
        self.assertFalse(hasattr(repeated, "submission_event"))
        self.assertTrue(SubmissionEvent.objects.filter(pk=prior_attempt.submission_event.pk).exists())

    def test_exclude_previous_avoids_other_active_frozen_assignment(self):
        first_plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first_plan,
            class_group=self.class_group,
        )
        generate_preview(
            first_plan,
            self.teacher,
            expected_revision=first_plan.plan_revision,
        )
        first_plan.refresh_from_db()
        first_task = commit_publish_plan(
            first_plan,
            self.teacher,
            expected_revision=first_plan.plan_revision,
        )[0]
        reserved_id = first_task.participant_snapshots.get(
            student=self.student
        ).assignment_snapshot[0]["source_question_id"]

        concept = self.experiment.sampling_kcs.get().concept
        available = Question.objects.create(
            bank=self.experiment.question_bank,
            sequence=2,
            code="Q02",
            question_type=Question.Type.SHORT,
            text="未被其他任务分配的题",
            correct_answer="答案",
        )
        QuestionConcept.objects.create(question=available, concept=concept, is_primary=True)
        later = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=self.experiment.question_bank,
            name="后续排重实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
            cross_experiment_item_policy=Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS,
        )
        ExperimentSamplingKC.objects.create(experiment=later, concept=concept)
        later_plan = get_or_create_publish_plan(later, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later_plan,
            class_group=self.class_group,
        )

        preview = generate_preview(
            later_plan,
            self.teacher,
            expected_revision=later_plan.plan_revision,
        )

        selected_id = preview.class_results.get().participants.get().items.get().source_question_id
        self.assertNotEqual(selected_id, reserved_id)
        self.assertEqual(selected_id, available.pk)

    def test_frozen_total_count_preview_reports_filtered_pool_shortage(self):
        self.experiment.cross_experiment_item_policy = (
            Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS
        )
        self.experiment.save(update_fields=["cross_experiment_item_policy"])

        first_plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first_plan,
            class_group=self.class_group,
        )
        generate_preview(
            first_plan,
            self.teacher,
            expected_revision=first_plan.plan_revision,
        )
        first_plan.refresh_from_db()
        commit_publish_plan(
            first_plan,
            self.teacher,
            expected_revision=first_plan.plan_revision,
        )

        concept = self.experiment.sampling_kcs.get().concept
        occupying_experiment = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=self.experiment.question_bank,
            name="并行占用实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
            cross_experiment_item_policy=(
                Experiment.CrossExperimentItemPolicy.UNRESTRICTED
            ),
        )
        ExperimentSamplingKC.objects.create(
            experiment=occupying_experiment,
            concept=concept,
        )
        occupying_plan = get_or_create_publish_plan(occupying_experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=occupying_plan,
            class_group=self.class_group_b,
        )
        generate_preview(
            occupying_plan,
            self.teacher,
            expected_revision=occupying_plan.plan_revision,
        )
        occupying_plan.refresh_from_db()
        commit_publish_plan(
            occupying_plan,
            self.teacher,
            expected_revision=occupying_plan.plan_revision,
        )

        later_plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later_plan,
            class_group=self.class_group_b,
        )

        with self.assertRaises(SamplingValidationError) as raised:
            generate_preview(
                later_plan,
                self.teacher,
                expected_revision=later_plan.plan_revision,
            )

        self.assertEqual(raised.exception.issues[0]["code"], "NEW_POOL_INSUFFICIENT")
        self.assertEqual(raised.exception.issues[0]["student_id"], self.student_b.pk)

    def test_preview_is_non_formal_then_commit_creates_one_frozen_release_mapping(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )

        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        self.assertEqual(preview.class_results.count(), 1)
        self.assertEqual(preview.class_results.first().participants.count(), 1)
        preview_participant = preview.class_results.first().participants.get()
        self.assertEqual(len(preview_participant.sampling_seed), 64)
        self.assertEqual(len(preview_participant.ordering_seed), 64)
        self.assertNotEqual(
            preview_participant.sampling_seed,
            preview_participant.ordering_seed,
        )
        self.assertEqual(
            preview_participant.sampling_algorithm_version,
            "task_root_hmac_v2",
        )
        self.assertFalse(ClassExperimentTask.objects.exists())

        plan.refresh_from_db()
        tasks = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.FROZEN)
        self.assertEqual(
            self.experiment.frozen_release_snapshot.configuration[
                "confidence_scale_version"
            ],
            "v2",
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(ReleaseSnapshotQuestionMapping.objects.count(), 1)
        self.assertEqual(tasks[0].participant_snapshots.count(), 1)
        participant_snapshot = tasks[0].participant_snapshots.get()
        self.assertEqual(
            participant_snapshot.sampling_seed,
            preview_participant.sampling_seed,
        )
        self.assertEqual(
            participant_snapshot.ordering_seed,
            preview_participant.ordering_seed,
        )
        self.assertEqual(
            participant_snapshot.sampling_algorithm_version,
            preview_participant.sampling_algorithm_version,
        )
        self.assertEqual(len(participant_snapshot.assignment_snapshot), 1)
        preview.class_results.first().participants.update(
            sampling_seed="f" * 64,
            ordering_seed="e" * 64,
            sampling_algorithm_version="mutated-preview",
        )
        session = get_or_create_session(self.experiment, self.student)
        self.assertEqual(session.class_task_id, tasks[0].pk)
        self.assertEqual(session.assignments.count(), 1)
        self.assertEqual(session.sampling_seed, participant_snapshot.sampling_seed)
        self.assertEqual(session.ordering_seed, participant_snapshot.ordering_seed)
        self.assertEqual(
            session.sampling_algorithm_version,
            participant_snapshot.sampling_algorithm_version,
        )
        self.assertEqual(
            get_or_create_session(self.experiment, self.student).pk,
            session.pk,
        )

    def test_new_course_short_answer_submission_starts_pending(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]
        session = get_or_create_session(
            self.experiment,
            self.student,
            class_task=task,
        )
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "与参考答案不同的合理表达")

        submitted_attempt = submit_attempt(attempt.pk, uuid.uuid4())

        submission = SubmissionEvent.objects.get(attempt=submitted_attempt)
        grade = submission.grade_records.get(version=1)
        self.assertEqual(submission.grading_status, SubmissionEvent.GradingStatus.PENDING)
        self.assertIsNone(submission.is_correct)
        self.assertIsNone(submission.score)
        self.assertIsNone(submitted_attempt.is_correct)
        self.assertIsNone(submitted_attempt.score)
        self.assertEqual(grade.validity_status, "candidate")
        self.assertEqual(grade.grading_method, "manual_binary_v1")
        self.assertEqual(grade.grading_version, "manual_binary_v1")
        self.assertEqual(str(grade.max_score), "1.00")

    def test_preview_uses_the_normalized_total_count_sampling_configuration(self):
        concept = Concept.objects.get(course=self.course, name="KC-A")
        second = Question.objects.create(
            bank=self.experiment.question_bank,
            sequence=2,
            code="Q02",
            question_type=Question.Type.SHORT,
            text="第二道候选题",
            correct_answer="答案",
        )
        QuestionConcept.objects.create(
            question=second,
            concept=concept,
            is_primary=True,
        )
        self.client.force_login(self.teacher)
        legacy_publish = self.client.post(
            reverse(
                "courses:experiment_publish",
                args=[self.course.pk, self.experiment.pk],
            )
        )
        self.assertEqual(legacy_publish.status_code, 409)
        self.assertContains(
            legacy_publish,
            "必须通过班级发布计划",
            status_code=409,
        )
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )

        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        participant = preview.class_results.get().participants.get()
        self.assertEqual(participant.item_count, 1)
        self.assertEqual(participant.items.count(), 1)

        plan.refresh_from_db()
        commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        self.experiment.refresh_from_db()
        release = self.experiment.frozen_release_snapshot
        configuration = release.configuration
        self.assertEqual(
            configuration["short_answer_grading_policy"],
            "manual_binary_v1",
        )
        self.assertEqual(configuration["sampling_mode"], "total_count")
        self.assertEqual(configuration["sampling_total_count"], 1)
        self.assertEqual(configuration["candidate_scope"], "selected_kcs")
        self.assertEqual(configuration["sampling"]["candidate_scope"], "selected_kcs")
        self.assertEqual(
            configuration["sampling"]["schema_version"],
            "sampling-snapshot-v4",
        )
        self.assertEqual(
            configuration["sampling"]["seed_derivation_version"],
            "task_root_hmac_v2",
        )
        self.assertNotIn("taxonomy_snapshot", configuration)
        self.assertEqual(configuration["sampling_kcs"][0]["kc_code"], concept.code)
        self.assertEqual(len(configuration["candidate_resolution"]), 2)
        snapshot = release.question_snapshots.order_by("source_sequence").first()
        self.assertEqual(snapshot.primary_kc_id_snapshot, concept.pk)
        self.assertEqual(snapshot.primary_kc_path, "KC-A")
        self.assertEqual(snapshot.all_kc_paths, ["KC-A"])
        self.assertEqual(snapshot.root_kc_code, concept.code)
        self.assertEqual(snapshot.kc_mapping_schema_version, "question-kc-tree-v2")
        self.assertEqual(snapshot.kc_mapping_snapshot["nodes"][0]["kc_code"], concept.code)
        self.assertEqual(len(snapshot.kc_mapping_hash), 64)
        original_text = snapshot.text
        snapshot.text = "不得篡改的题干"
        with self.assertRaisesMessage(ValidationError, "题目快照不可修改"):
            snapshot.save(update_fields=["text"])
        with self.assertRaisesMessage(ValidationError, "题目快照不可修改"):
            QuestionSnapshot.objects.filter(pk=snapshot.pk).update(
                text="不得批量篡改的题干"
            )
        with self.assertRaisesMessage(ValidationError, "题目快照不可删除"):
            snapshot.delete()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.text, original_text)

        self.experiment.sampling_total_count = 2
        with self.assertRaisesMessage(
            ValidationError,
            "实验定义已冻结",
        ):
            self.experiment.save(update_fields=["sampling_total_count"])

        self.experiment.refresh_from_db()
        self.experiment.status = Experiment.Status.DRAFT
        with self.assertRaisesMessage(ValidationError, "不能退回草稿"):
            self.experiment.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "不能退回草稿"):
            Experiment.objects.filter(pk=self.experiment.pk).update(
                status=Experiment.Status.DRAFT
            )

        with self.assertRaisesMessage(ValidationError, "实验定义已冻结"):
            Experiment.objects.filter(pk=self.experiment.pk).update(
                sampling_total_count=2,
            )
        QuerySet.update(
            Experiment.objects.filter(pk=self.experiment.pk),
            sampling_total_count=2,
        )
        self.client.force_login(self.teacher)
        detail = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/"
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["sampling_total_count"], 1)
        self.assertEqual(detail.json()["sampling_mode"], "total_count")
        Concept.objects.filter(pk=concept.pk).update(name="已被修改的 KC")
        later = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later,
            class_group=self.class_group_b,
        )

        later_preview = generate_preview(
            later,
            self.teacher,
            expected_revision=later.plan_revision,
        )

        self.assertEqual(
            later_preview.class_results.get().participants.get().item_count,
            1,
        )

    def test_type_quotas_are_frozen_and_attributed_in_participant_assignments(self):
        concept = Concept.objects.get(course=self.course, name="KC-A")
        second = Question.objects.create(
            bank=self.experiment.question_bank,
            sequence=2,
            code="Q02-TYPE",
            question_type=Question.Type.SINGLE,
            text="题型配额候选题",
            correct_answer="A",
        )
        QuestionOption.objects.create(
            question=second,
            key="A",
            text="正确选项",
            display_order=1,
        )
        QuestionOption.objects.create(
            question=second,
            key="B",
            text="干扰选项",
            display_order=2,
        )
        QuestionConcept.objects.create(
            question=second,
            concept=concept,
            is_primary=True,
        )
        self.experiment.sampling_total_count = 2
        self.experiment.save(update_fields=["sampling_total_count"])
        ExperimentQuestionQuota.objects.filter(
            experiment=self.experiment,
            question_type=Question.Type.SHORT,
        ).update(amount=1)
        ExperimentQuestionQuota.objects.filter(
            experiment=self.experiment,
            question_type=Question.Type.SINGLE,
        ).update(amount=1)
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )

        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        participant = preview.class_results.get().participants.get()
        sources = [
            item.sampling_source
            for item in participant.items.order_by("assigned_sequence_index")
        ]
        self.assertEqual(
            {source["quota_question_type"] for source in sources},
            {Question.Type.SHORT, Question.Type.SINGLE},
        )
        self.assertTrue(
            all(
                source["algorithm_version"] == "deterministic-scope-type-quota-v4"
                for source in sources
            )
        )

        plan.refresh_from_db()
        tasks = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        self.experiment.refresh_from_db()
        configuration = self.experiment.frozen_release_snapshot.configuration
        self.assertEqual(configuration["sampling"]["schema_version"], "sampling-snapshot-v4")
        self.assertEqual(
            configuration["sampling"]["sampling_algorithm_version"],
            "deterministic-scope-type-quota-v4",
        )
        self.assertEqual(
            configuration["sampling"]["type_quotas"],
            [
                {"question_type": Question.Type.SINGLE, "amount": 1},
                {"question_type": Question.Type.SHORT, "amount": 1},
            ],
        )
        assignment_sources = {
            row["sampling_source"]["quota_question_type"]
            for row in tasks[0].participant_snapshots.get().assignment_snapshot
        }
        self.assertEqual(
            assignment_sources,
            {Question.Type.SHORT, Question.Type.SINGLE},
        )

    def test_question_kc_mapping_change_invalidates_a_sampling_preview_before_commit(self):
        concept = Concept.objects.get(course=self.course, name="KC-A")
        self.experiment.sampling_mode = Experiment.SamplingMode.TOTAL_COUNT
        self.experiment.sampling_total_count = 1
        self.experiment.save(
            update_fields=["sampling_mode", "sampling_total_count"]
        )
        self.experiment.sampling_kcs.all().delete()
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        question = self.experiment.question_bank.questions.get()
        primary_link = question.concept_links.get(is_primary=True)
        child = Concept.objects.create(course=self.course, name="新增二级 KC")
        QuestionConcept.objects.create(
            question=question,
            concept=child,
            parent_link=primary_link,
            display_order=1,
        )

        with self.assertRaises(PublicationConflict) as raised:
            commit_publish_plan(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )

        self.assertEqual(raised.exception.code, "PREVIEW_INVALIDATED")

    def test_schedule_change_invalidates_the_selected_preview_before_commit(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        preview = generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()

        save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=timezone.now() + timedelta(hours=1),
            default_closes_at=timezone.now() + timedelta(hours=2),
            overrides={},
        )

        preview.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(preview.status, preview.Status.INVALIDATED)
        with self.assertRaises(PublicationConflict) as raised:
            commit_publish_plan(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )
        self.assertEqual(raised.exception.code, "PREVIEW_INVALIDATED")

    def test_commit_rejects_a_preview_from_an_older_plan_revision(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        preview = generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        PublishPlan.objects.filter(pk=plan.pk).update(plan_revision=plan.plan_revision + 1)
        plan.refresh_from_db()

        with self.assertRaises(PublicationConflict) as raised:
            commit_publish_plan(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )

        self.assertEqual(raised.exception.code, "PREVIEW_INVALIDATED")

    def test_preview_input_hash_includes_the_effective_class_schedule(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(publish_plan=plan, class_group=self.class_group)
        opens_at = timezone.now() + timedelta(hours=1)
        first_closes_at = opens_at + timedelta(hours=1)
        second_closes_at = opens_at + timedelta(hours=2)
        save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=opens_at,
            default_closes_at=first_closes_at,
            overrides={},
        )
        plan.refresh_from_db()
        first_preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        plan.refresh_from_db()

        save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=opens_at,
            default_closes_at=second_closes_at,
            overrides={},
        )
        plan.refresh_from_db()
        second_preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        self.assertNotEqual(first_preview.input_hash, second_preview.input_hash)

    def test_later_class_fixed_order_restores_canonical_order_after_frozen_sampling(self):
        concept = Concept.objects.get(course=self.course, name="KC-A")
        for sequence in (2, 3):
            question = Question.objects.create(
                bank=self.experiment.question_bank,
                sequence=sequence,
                code=f"Q{sequence:02d}",
                question_type=Question.Type.SHORT,
                text=f"候选题 {sequence}",
                correct_answer="答案",
            )
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
        self.experiment.sampling_mode = Experiment.SamplingMode.TOTAL_COUNT
        self.experiment.sampling_total_count = 2
        self.experiment.ordering_mode = Experiment.Ordering.FIXED
        self.experiment.save(
            update_fields=[
                "sampling_mode",
                "sampling_total_count",
                "ordering_mode",
            ]
        )
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
        )
        generate_preview(first, self.teacher, expected_revision=first.plan_revision)
        first.refresh_from_db()
        commit_publish_plan(
            first,
            self.teacher,
            expected_revision=first.plan_revision,
        )

        later = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later,
            class_group=self.class_group_b,
        )
        with patch(
            "apps.experiments.publication_services.random.Random.sample",
            side_effect=lambda population, count: list(reversed(population))[:count],
        ):
            preview = generate_preview(
                later,
                self.teacher,
                expected_revision=later.plan_revision,
            )

        participant = preview.class_results.get().participants.get()
        sequences = list(
            participant.items.order_by("assigned_sequence_index").values_list(
                "source_question__sequence",
                flat=True,
            )
        )
        self.assertEqual(sequences, sorted(sequences))

    def test_later_class_publication_reuses_the_frozen_release_and_mapping(self):
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
        )
        generate_preview(first, self.teacher, expected_revision=first.plan_revision)
        first.refresh_from_db()
        first_task = commit_publish_plan(
            first,
            self.teacher,
            expected_revision=first.plan_revision,
        )[0]

        later = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later,
            class_group=self.class_group_b,
        )
        generate_preview(later, self.teacher, expected_revision=later.plan_revision)
        later.refresh_from_db()
        later_task = commit_publish_plan(
            later,
            self.teacher,
            expected_revision=later.plan_revision,
        )[0]

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.FROZEN)
        self.assertEqual(first_task.release_snapshot_id, later_task.release_snapshot_id)
        self.assertEqual(ReleaseSnapshotQuestionMapping.objects.count(), 1)

    def test_commit_rolls_back_every_formal_record_when_a_later_class_fails(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        for class_group in (self.class_group, self.class_group_b):
            PublishPlanClass.objects.create(
                publish_plan=plan,
                class_group=class_group,
            )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()

        from apps.experiments import publication_services

        original_create = publication_services._create_task_from_preview
        call_count = 0

        def fail_on_second_class(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated second class failure")
            return original_create(*args, **kwargs)

        with patch(
            "apps.experiments.publication_services._create_task_from_preview",
            side_effect=fail_on_second_class,
        ):
            with self.assertRaisesRegex(RuntimeError, "second class failure"):
                commit_publish_plan(
                    plan,
                    self.teacher,
                    expected_revision=plan.plan_revision,
                )

        self.experiment.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertIsNone(self.experiment.frozen_release_snapshot_id)
        self.assertEqual(plan.status, PublishPlan.Status.PREVIEWED)
        self.assertFalse(ExperimentVersion.objects.exists())
        self.assertFalse(ReleaseSnapshotQuestionMapping.objects.exists())
        self.assertFalse(ClassExperimentTask.objects.exists())
        self.assertFalse(ExperimentClassSnapshot.objects.exists())
        self.assertFalse(ExperimentVersionParticipantSnapshot.objects.exists())
        self.assertFalse(ScheduleRevision.objects.exists())

    def test_new_plan_reuses_the_same_class_randomization_root_and_output(self):
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
        )
        first_preview = generate_preview(
            first,
            self.teacher,
            expected_revision=first.plan_revision,
        )
        first_result = first_preview.class_results.get()
        PublishPlan.objects.filter(pk=first.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        second = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=second,
            class_group=self.class_group,
        )
        second_preview = generate_preview(
            second,
            self.teacher,
            expected_revision=second.plan_revision,
        )
        second_result = second_preview.class_results.get()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            first_result.randomization_identity_id,
            second_result.randomization_identity_id,
        )
        self.assertEqual(first_result.output_hash, second_result.output_hash)

    def test_later_class_preview_uses_the_frozen_question_snapshot_after_source_edit(self):
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
        )
        generate_preview(first, self.teacher, expected_revision=first.plan_revision)
        first.refresh_from_db()
        commit_publish_plan(
            first,
            self.teacher,
            expected_revision=first.plan_revision,
        )
        mapping = ReleaseSnapshotQuestionMapping.objects.get()
        Question.objects.filter(pk=mapping.source_question_id).update(
            text="发布后被编辑的题库文本"
        )

        later = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later,
            class_group=self.class_group_b,
        )

        preview = generate_preview(
            later,
            self.teacher,
            expected_revision=later.plan_revision,
        )

        item = preview.class_results.get().participants.get().items.get()
        self.assertEqual(item.source_question_id, mapping.source_question_id)
        self.assertEqual(
            item.question_release_fingerprint,
            mapping.question_release_fingerprint,
        )

    def test_a_plus_task_preserves_the_student_start_draft_submit_contract(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        accept_current_research_consent(
            student=self.student,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        )
        self.client.force_login(self.student)

        start = self.client.post(
            reverse("assessment:start", args=[self.experiment.pk])
        )
        self.assertEqual(start.status_code, 302)
        attempt = Attempt.objects.get(
            session__student=self.student,
            session__version__experiment=self.experiment,
        )
        self.assertEqual(
            start["Location"],
            reverse(
                "assessment:question",
                args=[attempt.session_id, attempt.assignment_id],
            ),
        )
        question = self.client.get(
            f"/api/v1/student/sessions/{attempt.session_id}/questions/"
            f"{attempt.assignment_id}/"
        )
        self.assertEqual(question.status_code, 200)
        self.assertEqual(question.json()["question"]["text"], "发布预览题")

        draft = self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": "答案"}),
            content_type="application/json",
        )
        self.assertEqual(draft.status_code, 200)
        self.assertTrue(draft.json()["success"])
        self.assertIn("response_snapshot_id", draft.json())

        submitted = self.client.post(
            reverse("assessment:submit", args=[attempt.pk]),
            data=json.dumps({"submission_token": str(uuid.uuid4())}),
            content_type="application/json",
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertTrue(submitted.json()["success"])
        self.assertEqual(submitted.json()["status"], "submitted")
        event_types = set(
            AssessmentEvent.objects.filter(session=attempt.session).values_list(
                "event_type", flat=True
            )
        )
        self.assertTrue(
            {"session_started", "item_entered", "response_committed", "attempt_closed"}
            <= event_types
        )

    def test_same_stable_student_cannot_be_previewed_for_a_second_class_task(self):
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
        )
        generate_preview(first, self.teacher, expected_revision=first.plan_revision)
        first.refresh_from_db()
        commit_publish_plan(first, self.teacher, expected_revision=first.plan_revision)
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student,
        ).update(class_group=self.class_group_b)
        later = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=later,
            class_group=self.class_group_b,
        )

        with self.assertRaises(PublicationConflict) as raised:
            generate_preview(later, self.teacher, expected_revision=later.plan_revision)

        self.assertEqual(raised.exception.code, "PARTICIPANT_ALREADY_ASSIGNED")

    def test_submission_received_before_manual_close_is_allowed_to_finish(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, "答案")
        manual_close_task(task, self.teacher, reason="teacher_ended")
        task.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(session.status, ExperimentSession.Status.IN_PROGRESS)

        submitted = submit_attempt(
            attempt.pk,
            uuid.uuid4(),
            request_received_at=task.manual_closed_at - timedelta(milliseconds=1),
        )

        self.assertEqual(submitted.status, Attempt.Status.SUBMITTED)

    def test_process_event_batch_uses_request_arrival_time_after_manual_close(self):
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student,
        ).update(enrolled_at=timezone.now() - timedelta(minutes=1))
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)
        manual_close_task(task, self.teacher, reason="teacher_ended")
        task.refresh_from_db()

        def document():
            now = timezone.now().isoformat()
            return {
                "client_instance_id": str(uuid.uuid4()),
                "events": [{
                    "event_type": "activity_heartbeat",
                    "event_version": 1,
                    "session_id": session.pk,
                    "attempt_id": attempt.pk,
                    "client_sequence_no": 1,
                    "correlation_id": None,
                    "client_wall_time_utc": now,
                    "client_monotonic_ms": "1",
                    "client_time_origin": now,
                    "time_sync_id": None,
                    "payload": {
                        "attempt_id": attempt.pk,
                        "document_visible": True,
                        "window_focused": True,
                        "last_device_interaction_age_ms": 0,
                    },
                    "quality_flags": [],
                    "app_version": "test",
                }],
            }

        acknowledgements = append_event_batch(
            student=self.student,
            document=document(),
            request_received_at=task.manual_closed_at - timedelta(milliseconds=1),
        )
        self.assertEqual(len(acknowledgements), 1)

        with self.assertRaises(ProcessConflictError) as raised:
            append_event_batch(
                student=self.student,
                document=document(),
                request_received_at=task.manual_closed_at,
            )
        self.assertEqual(raised.exception.code, "task_terminated")

    def test_preview_export_contains_all_participants(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        self.client.force_login(self.teacher)
        url = reverse(
            "courses:experiment_publish_plan",
            args=[self.course.pk, self.experiment.pk, plan.pk, 2],
        )

        export = self.client.get(f"{url}?preview_export=1")

        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn("A-001", export.content.decode("utf-8-sig"))

    def test_complete_export_carries_release_task_preview_and_deadline_lineage_without_secrets(self):
        concept = Concept.objects.get(course=self.course, name="KC-A")
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)

        export_snapshot = create_export_snapshot(
            course=self.course,
            experiment=self.experiment,
            created_by=self.teacher,
        )
        job = ExportJob.objects.create(
            course=self.course,
            experiment=self.experiment,
            snapshot=export_snapshot,
            created_by=self.teacher,
            package="complete",
        )

        run_export_job(job)

        with zipfile.ZipFile(job.artifact_path) as archive:
            names = set(archive.namelist())
            session_header = archive.read("02_core/sessions.csv").decode("utf-8-sig").splitlines()[0]
            attempt_header = archive.read("02_core/attempts.csv").decode("utf-8-sig").splitlines()[0]
            task_csv = archive.read("01_design_metadata/class_experiment_tasks.csv").decode("utf-8-sig")
            release_csv = archive.read("01_design_metadata/release_metadata.csv").decode("utf-8-sig")
            item_snapshot_lines = archive.read("01_design_metadata/item_snapshots.csv").decode("utf-8-sig").splitlines()
            item_snapshot_header = item_snapshot_lines[0]
            participant_csv = archive.read(
                "01_design_metadata/participant_snapshots.csv"
            ).decode("utf-8-sig")
            kc_v2 = archive.read(
                "01_design_metadata/kc_dictionary_v2.csv"
            ).decode("utf-8-sig")
            structure_v2 = archive.read(
                "01_design_metadata/question_kc_structure_v2.csv"
            ).decode("utf-8-sig")
            q_matrix_v2 = archive.read(
                "01_design_metadata/q_matrix_v2.csv"
            ).decode("utf-8-sig")
            attribution_v2 = archive.read(
                "01_design_metadata/participant_question_attribution_v2.csv"
            ).decode("utf-8-sig")
        self.assertIn("01_design_metadata/release_metadata.csv", names)
        self.assertIn("01_design_metadata/publish_preview_runs.csv", names)
        self.assertIn("initial_effective_deadline_at", session_header)
        self.assertIn("item_deadline_at", attempt_header)
        self.assertIn("root_seed_fingerprint", task_csv)
        self.assertIn("sampling_mode", release_csv.splitlines()[0])
        self.assertIn("candidate_resolution", release_csv.splitlines()[0])
        self.assertNotIn("sampling_seed", release_csv)
        self.assertIn("primary_kc_path", item_snapshot_header)
        self.assertEqual(len(item_snapshot_lines), 2)
        participant_lines = participant_csv.splitlines()
        self.assertEqual(len(participant_lines), 2)
        self.assertIn("sampling_seed_sha256", participant_lines[0])
        self.assertIn("sampling_algorithm_version", participant_lines[0])
        self.assertIn("assignment_snapshot", participant_lines[0])
        self.assertNotIn("sampling_seed,", participant_lines[0])
        self.assertIn("all_kc_paths", item_snapshot_header)
        self.assertIn("kc_code", kc_v2.splitlines()[0])
        self.assertIn("mapping_hash", structure_v2.splitlines()[0])
        self.assertIn(concept.code, q_matrix_v2.splitlines()[0])
        self.assertIn("quota_kc_code", attribution_v2.splitlines()[0])
        self.assertIn("quota_question_type", attribution_v2.splitlines()[0])
        self.assertNotIn("encrypted_root_seed", task_csv)
        self.assertNotIn("randomization_subject_key", task_csv)
