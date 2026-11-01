from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from apps.accounts.models import ClassGroup
from apps.api.test_support import domain_payload
from apps.assessment import test_manual_short_answer_grading as grading_fixture
from apps.assessment.models import SubmissionEvent
from .experiment_activity import experiment_activity_map
from .models import (
    ClassExperimentTask, Experiment, ExperimentClassSnapshot,
    ExperimentVersion, ScheduleRevision,
)
from .publication_services import _class_experiment_task_publication_scope


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
