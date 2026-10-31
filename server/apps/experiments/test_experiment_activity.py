from unittest.mock import patch

from django.test import TestCase

from apps.api.test_support import domain_payload
from apps.assessment import test_manual_short_answer_grading as grading_fixture
from apps.assessment.models import SubmissionEvent
from .models import ExperimentVersion, ScheduleRevision


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
        self.assertEqual(domain_payload(self.client.get(url))["items"][0]["activity"]["pending_grading_count"], 0)

    def test_activity_is_not_exposed_outside_authorized_course(self):
        self.client.force_login(self.other_teacher)
        url = f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
        self.assertEqual(self.client.get(url).status_code, 404)
