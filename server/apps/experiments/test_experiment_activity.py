from unittest.mock import patch
from datetime import timedelta

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ClassGroup, User
from apps.api.test_support import domain_payload
from apps.assessment import test_manual_short_answer_grading as grading_fixture
from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.models import ExperimentSession, SubmissionEvent
from .experiment_activity import experiment_activity_map
from .models import (
    ClassExperimentTask, Experiment, ExperimentClassSnapshot,
    ExperimentVersion, ExperimentVersionParticipantSnapshot, ScheduleRevision,
)
from .publication_services import _class_experiment_task_publication_scope
from .services import _class_experiment_task_terminal_update_scope


class ExperimentActivityApiTests(TestCase):
    def setUp(self):
        create_version = ExperimentVersion.objects.create

        def timed_version(**kwargs):
            kwargs["configuration"].update(time_mode="total", total_time_limit_minutes=30)
            return create_version(**kwargs)

        with patch.object(ExperimentVersion.objects, "create", side_effect=timed_version):
            grading_fixture.ManualShortAnswerGradingTests.setUp(self)

    def test_activity_counts_pending_submissions_and_uses_current_schedule(self):
        self.client.force_login(self.teacher)
        ScheduleRevision.objects.create(class_task=self.task, revision_no=1)
        latest = ScheduleRevision.objects.create(
            class_task=self.task, revision_no=2, total_time_limit_seconds=2700,
        )
        url = f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
        activity = domain_payload(self.client.get(url))["items"][0]["activity"]
        self.assertEqual(activity["pending_grading_count"], 1)
        self.assertEqual(activity["grading_url"],
            f"/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/class-tasks/{self.task.pk}/grading/")
        self.assertEqual(activity["schedules"][0]["total_time_limit_minutes"], 45)
        self.assertEqual(activity["schedules"][0]["opens_at"], latest.opens_at)
        SubmissionEvent.objects.filter(pk=self.submission.pk).update(grading_status=SubmissionEvent.GradingStatus.COMPLETED)
        completed_activity = domain_payload(self.client.get(url))["items"][0]["activity"]
        self.assertEqual(completed_activity["pending_grading_count"], 0)
        self.assertEqual(completed_activity["grading_url"], activity["grading_url"])

    def create_empty_task(self, *, class_name="A", experiment=None, policy="manual_binary_v1"):
        experiment = experiment or self.experiment
        version = self.version
        if experiment != self.experiment or policy != "manual_binary_v1":
            version = ExperimentVersion.objects.create(
                experiment=experiment, version_number=2,
                configuration={"short_answer_grading_policy": policy},
                published_by=self.teacher,
            )
        class_group = ClassGroup.objects.create(course=self.course, name=class_name)
        class_snapshot = ExperimentClassSnapshot.objects.create(
            version=version, source_class_group=class_group,
            name_snapshot=class_name, academic_year_snapshot="2026",
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        with transaction.atomic(), _class_experiment_task_publication_scope():
            return ClassExperimentTask.objects.create(
                class_group=class_group, experiment=experiment,
                release_snapshot=version, class_snapshot=class_snapshot,
                published_at=version.published_at, published_by=self.teacher,
            )

    def test_class_task_list_reads_submissions_once_for_multiple_classes(self):
        empty = self.create_empty_task()
        for task in (self.task, empty):
            ScheduleRevision.objects.create(class_task=task, revision_no=1)
        ExperimentVersionParticipantSnapshot.objects.create(
            version=self.version, class_task=empty, student=self.student,
            assignment_snapshot=[{"question_snapshot_id": self.snapshot.pk}],
        )
        self.client.force_login(self.teacher)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(f"/api/v1/teacher/experiments/{self.experiment.pk}/class-tasks/")
        rows = {row["id"]: row["grading"] for row in domain_payload(response)["items"]}
        self.assertEqual(rows[self.task.pk], dict(enabled=True, short_answer_questions=1, submitted=1, graded=0, pending=1))
        self.assertEqual(rows[empty.pk], dict(enabled=True, short_answer_questions=1, submitted=0, graded=0, pending=0))
        sql = [q["sql"] for q in captured if 'FROM "assessment_submissionevent"' in q["sql"]]
        self.assertEqual(len(sql), 1)

    def test_activity_materializes_only_the_effective_revision_per_task(self):
        for number in range(1, 5):
            ScheduleRevision.objects.create(class_task=self.task, revision_no=number)
        original = DeadlineDecisionService.schedule_at
        observed = []

        def inspect_schedule(task, at):
            observed.append([row.revision_no for row in task.latest_schedule_revisions])
            return original(task, at)

        with patch.object(DeadlineDecisionService, "schedule_at", side_effect=inspect_schedule):
            experiment_activity_map([self.experiment], can_grade=True)
        self.assertEqual(observed, [[4]])

    def test_manual_grading_entry_is_available_before_first_submission(self):
        empty_experiment = Experiment.objects.create(
            course=self.course, owner=self.teacher, question_bank=self.bank,
            name="尚无提交的人工批改实验",
        )
        task = self.create_empty_task(experiment=empty_experiment)
        self.client.force_login(self.teacher)
        collection = domain_payload(self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/",
        ))
        activity = next(item["activity"] for item in collection["items"]
                        if item["id"] == empty_experiment.pk)
        self.assertEqual(activity["pending_grading_count"], 0)
        self.assertEqual(activity["grading_url"],
            f"/teacher/courses/{self.course.pk}/experiments/{empty_experiment.pk}/class-tasks/{task.pk}/grading/")
        overview = self.client.get(f"/api/v1/teacher/class-tasks/{task.pk}/grading/")
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(domain_payload(overview)["totals"]["submitted"], 0)

    def test_pending_task_is_preferred_over_empty_manual_tasks(self):
        self.class_group.name = "B"
        self.class_group.save(update_fields=["name"])
        self.create_empty_task(class_name="A")
        self.create_empty_task(class_name="Z")
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity["pending_grading_count"], 1)
        self.assertEqual(activity["grading_url"],
            f"/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/class-tasks/{self.task.pk}/grading/")

    def test_grading_entry_requires_manage_permission_even_when_pending(self):
        activity = experiment_activity_map([self.experiment], can_grade=False)[self.experiment.pk]
        self.assertIsNone(activity["grading_url"])
        SubmissionEvent.objects.filter(pk=self.submission.pk).update(
            grading_status=SubmissionEvent.GradingStatus.COMPLETED,
        )
        activity = experiment_activity_map([self.experiment], can_grade=False)[self.experiment.pk]
        self.assertIsNone(activity["grading_url"])

    def test_non_manual_task_does_not_expose_grading_entry(self):
        automatic_experiment = Experiment.objects.create(
            course=self.course, owner=self.teacher, question_bank=self.bank,
            name="自动评分实验",
        )
        self.create_empty_task(experiment=automatic_experiment, policy="exact_match_v1")
        activity = experiment_activity_map([automatic_experiment], can_grade=True)[automatic_experiment.pk]
        self.assertIsNone(activity["grading_url"])

    def test_activity_is_not_exposed_outside_authorized_course(self):
        self.client.force_login(self.other_teacher)
        url = f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_card_lifecycle_uses_class_schedules_instead_of_definition_status(self):
        now = timezone.now()
        ScheduleRevision.objects.create(class_task=self.task, revision_no=1,
            opens_at=now + timedelta(hours=1), closes_at=now + timedelta(hours=2))
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['lifecycle_status'], 'not_started')
        self.assertEqual(activity['lifecycle_label'], '未开始')
        self.assertEqual(activity['key_time_label'], '开始时间')
        self.assertEqual(activity['key_time'], now + timedelta(hours=1))

        ScheduleRevision.objects.create(class_task=self.task, revision_no=2,
            opens_at=now - timedelta(hours=2), closes_at=now - timedelta(hours=1))
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['lifecycle_status'], 'ended')
        self.assertEqual(activity['lifecycle_label'], '已结束')
        self.assertEqual(activity['key_time'], now - timedelta(hours=1))
        self.assertEqual(activity['pending_grading_count'], 1)

        future = self.create_empty_task(class_name='未来班级')
        ScheduleRevision.objects.create(class_task=future, revision_no=1,
            opens_at=now + timedelta(hours=1), closes_at=now + timedelta(hours=2))
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['lifecycle_status'], 'started')
        self.assertEqual(activity['key_time_label'], '最近截止')
        self.assertEqual(activity['key_time'], now + timedelta(hours=2))

    def test_card_lifecycle_honors_manual_closure_and_reopening(self):
        now = timezone.now()
        self.task.manual_closed_at = now - timedelta(minutes=1)
        with _class_experiment_task_terminal_update_scope():
            self.task.save(update_fields=['manual_closed_at'])
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['lifecycle_status'], 'ended')
        self.assertEqual(activity['key_time'], self.task.manual_closed_at)
        ScheduleRevision.objects.create(class_task=self.task, revision_no=1,
            reopens_manual_close=True, closes_at=now + timedelta(hours=1))
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['lifecycle_status'], 'started')

    def test_card_counts_frozen_participants_without_prepared_or_expired_sessions(self):
        participant = ExperimentVersionParticipantSnapshot.objects.create(
            version=self.version, class_task=self.task, student=self.student)
        ExperimentSession.objects.filter(class_task=self.task).update(participant_snapshot=participant,
            status=ExperimentSession.Status.COMPLETED, terminal_at=timezone.now())
        for index, status in enumerate([ExperimentSession.Status.PREPARED,
                ExperimentSession.Status.IN_PROGRESS, ExperimentSession.Status.SUBMITTING,
                ExperimentSession.Status.EXPIRED]):
            student = User.objects.create_user(username=f'card-student-{index}', role=User.Role.STUDENT)
            snapshot = ExperimentVersionParticipantSnapshot.objects.create(
                version=self.version, class_task=self.task, student=student)
            ExperimentSession.objects.create(version=self.version, class_task=self.task,
                participant_snapshot=snapshot, student=student, status=status,
                started_at=None if status == ExperimentSession.Status.PREPARED else timezone.now())
        User.objects.create_user(username='not-a-published-participant', role=User.Role.STUDENT)
        self.client.force_login(self.teacher)
        activity = domain_payload(self.client.get(
            f'/api/v1/teacher/courses/{self.course.pk}/experiments/'))['items'][0]['activity']
        self.assertEqual(activity['participant_count'], 5)
        self.assertEqual(activity['completed_count'], 1)
        self.assertEqual(activity['in_progress_count'], 2)
        self.assertEqual(activity['lifecycle_label'], '已开始')
        now = timezone.now()
        ScheduleRevision.objects.create(class_task=self.task, revision_no=1,
            opens_at=now - timedelta(hours=2), closes_at=now - timedelta(hours=1))
        ended = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(ended['in_progress_count'], 0)
        self.assertEqual(ended['completed_count'], 1)

    def test_participants_in_multiple_classes_are_counted_as_people(self):
        participant = ExperimentVersionParticipantSnapshot.objects.create(
            version=self.version, class_task=self.task, student=self.student)
        ExperimentSession.objects.filter(class_task=self.task).update(participant_snapshot=participant,
            status=ExperimentSession.Status.COMPLETED, terminal_at=timezone.now())
        other_task = self.create_empty_task(class_name='同一学生的另一班级任务')
        ExperimentVersionParticipantSnapshot.objects.create(
            version=self.version, class_task=other_task, student=self.student)
        activity = experiment_activity_map([self.experiment], can_grade=True)[self.experiment.pk]
        self.assertEqual(activity['participant_count'], 1)
        self.assertEqual(activity['completed_count'], 0)

    def test_unpublished_draft_has_no_formal_participant_counts(self):
        draft = Experiment.objects.create(course=self.course, owner=self.teacher,
            question_bank=self.bank, name='新建草稿')
        activity = experiment_activity_map([draft], can_grade=True)[draft.pk]
        self.assertEqual(activity['lifecycle_status'], 'draft')
        self.assertEqual(activity['lifecycle_label'], '草稿')
        self.assertEqual(activity['participant_count'], 0)
        self.assertIsNone(activity['key_time'])
