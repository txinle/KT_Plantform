from datetime import timedelta
import json
import uuid
import zipfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.analytics.models import ExportJob
from apps.analytics.services import create_export_snapshot, run_export_job
from apps.assessment.services import (
    SubmissionValidationError,
    get_or_create_session,
    record_visit,
    save_draft,
    submit_attempt,
)
from apps.assessment.models import AssessmentEvent
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    PublishPlan,
    PublishPlanClass,
    ReleaseSnapshotQuestionMapping,
    ScheduleRevision,
)
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
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept


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
        concept = Concept.objects.create(course=self.course, name="KC-A")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=bank,
            name="A+ 实验",
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
            schedule_configured=True,
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

    def test_setup_status_distinguishes_unset_from_explicit_open_ended_schedule(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()

        incomplete = publish_plan_setup_projection(plan)
        self.assertEqual(incomplete["setup_status"], "incomplete")
        self.assertEqual(
            incomplete["missing_requirements"],
            [f"schedule:{self.class_group.pk}"],
        )

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

    def test_preview_rejects_incomplete_setup_without_losing_the_draft(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan.refresh_from_db()

        with self.assertRaises(ValidationError):
            generate_preview(
                plan,
                self.teacher,
                expected_revision=plan.plan_revision,
            )

        plan.refresh_from_db()
        self.assertEqual(plan.status, PublishPlan.Status.DRAFT)
        self.assertIsNone(plan.preview_run_id)
        self.assertEqual(
            list(plan.class_plans.values_list("class_group_id", flat=True)),
            [self.class_group.pk],
        )

    def test_preview_is_non_formal_then_commit_creates_one_frozen_release_mapping(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
        )

        preview = generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        self.assertEqual(preview.class_results.count(), 1)
        self.assertEqual(preview.class_results.first().participants.count(), 1)
        self.assertFalse(ClassExperimentTask.objects.exists())

        plan.refresh_from_db()
        tasks = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.FROZEN)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(ReleaseSnapshotQuestionMapping.objects.count(), 1)
        self.assertEqual(tasks[0].participant_snapshots.count(), 1)
        session = get_or_create_session(self.experiment, self.student)
        self.assertEqual(session.class_task_id, tasks[0].pk)
        self.assertEqual(session.assignments.count(), 1)
        self.assertEqual(
            get_or_create_session(self.experiment, self.student).pk,
            session.pk,
        )

    def test_schedule_change_invalidates_the_selected_preview_before_commit(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
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
            schedule_configured=True,
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

    def test_later_class_publication_reuses_the_frozen_release_and_mapping(self):
        first = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=first,
            class_group=self.class_group,
            schedule_configured=True,
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
            schedule_configured=True,
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
                schedule_configured=True,
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
            schedule_configured=True,
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
            schedule_configured=True,
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
            schedule_configured=True,
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
            schedule_configured=True,
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

    def test_a_plus_task_preserves_the_legacy_student_start_draft_submit_contract(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        self.client.force_login(self.student)

        start = self.client.post(
            reverse("assessment:start", args=[self.experiment.pk])
        )
        self.assertEqual(start.status_code, 302)
        question_page = self.client.get(start["Location"])
        self.assertEqual(question_page.status_code, 200)
        self.assertContains(question_page, "发布预览题")
        attempt = question_page.context["attempt"]

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
            schedule_configured=True,
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
            schedule_configured=True,
        )

        with self.assertRaises(PublicationConflict) as raised:
            generate_preview(later, self.teacher, expected_revision=later.plan_revision)

        self.assertEqual(raised.exception.code, "PARTICIPANT_ALREADY_ASSIGNED")

    def test_manual_close_wins_over_a_submission_that_arrived_earlier(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
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

        with self.assertRaises(SubmissionValidationError) as raised:
            submit_attempt(
                attempt.pk,
                uuid.uuid4(),
                request_received_at=timezone.now() - timedelta(seconds=1),
            )

        self.assertEqual(raised.exception.reason_code, "task_terminated")

    def test_teacher_enters_the_two_step_publish_flow(self):
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse(
                "courses:experiment_publish_plan_start",
                args=[self.course.pk, self.experiment.pk],
            )
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"])
        self.assertContains(response, "01 班级与排期")
        self.assertContains(response, "02 检查并发布")
        self.assertNotContains(response, "03 抽题与顺序预览")

    def test_preview_step_shows_all_participants_and_offers_csv_export(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        self.client.force_login(self.teacher)
        url = reverse(
            "courses:experiment_publish_plan",
            args=[self.course.pk, self.experiment.pk, plan.pk, 2],
        )

        response = self.client.get(url)
        export = self.client.get(f"{url}?preview_export=1")

        self.assertContains(response, "A-001")
        self.assertContains(response, "确认并原子发布")
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn("A-001", export.content.decode("utf-8-sig"))

    def test_complete_export_carries_release_task_preview_and_deadline_lineage_without_secrets(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
            schedule_configured=True,
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        record_visit(session.pk, assignment.pk)

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
        self.assertIn("01_design_metadata/release_metadata.csv", names)
        self.assertIn("01_design_metadata/publish_preview_runs.csv", names)
        self.assertIn("initial_effective_deadline_at", session_header)
        self.assertIn("item_deadline_at", attempt_header)
        self.assertIn("root_seed_fingerprint", task_csv)
        self.assertNotIn("encrypted_root_seed", task_csv)
        self.assertNotIn("randomization_subject_key", task_csv)
