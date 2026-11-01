import re

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from . import test_group_snapshots as fixture
from .models import Experiment


class TeacherStudentPreviewTests(TestCase):
    def setUp(self):
        fixture.GroupSnapshotTests.setUp(self)
        self.client.force_login(self.teacher)

    def url(self, experiment):
        return f"/api/v1/teacher/courses/{self.course.pk}/experiments/{experiment.pk}/student-preview/"

    def assert_preview_is_read_only(self, experiment):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url(experiment))
        self.assertEqual(response.status_code, 200, response.content)
        writes = [query["sql"] for query in queries if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", query["sql"], re.I)]
        self.assertEqual(writes, [])
        body = response.content.decode()
        for forbidden in ("correct_answer", "accepted_answers", "sampling_seed", "ordering_seed", "student_number", "session_id"):
            self.assertNotIn(forbidden, body)
        return domain_payload(response)

    def test_draft_preview_uses_complete_questions_without_creating_publication_or_student_records(self):
        experiment = Experiment.objects.create(
            course=self.course, owner=self.teacher, question_bank=self.bank,
            name="模拟实验", selection_mode="all", sampling_mode=None,
        )
        payload = self.assert_preview_is_read_only(experiment)
        self.assertEqual(payload["source"], "draft")
        self.assertEqual(len(payload["units"]), 2)
        self.assertEqual(len(payload["units"][0]["group"]["members"]), 3)
        self.assertEqual(payload["units"][1]["question"]["question_type"], "fill_blank")
        self.assertEqual(payload["units"][1]["kind"], "fill_blank")
        self.assertEqual(self.assert_preview_is_read_only(experiment), payload)
        detail = domain_payload(self.client.get(self.url(experiment).replace("student-preview/", "")))
        self.assertEqual(detail["student_preview_url"],
            f"/teacher/courses/{self.course.pk}/experiments/{experiment.pk}/student-preview/")

    def test_published_preview_uses_frozen_material_and_not_later_bank_edits(self):
        experiment, _ = fixture.GroupSnapshotTests.publish(self)
        self.group.material = "Changed bank material"
        self.group.save(update_fields=["material"])
        payload = self.assert_preview_is_read_only(experiment)
        self.assertEqual(payload["source"], "frozen")
        self.assertEqual(payload["units"][0]["group"]["material"], "A frozen garden.")
        self.assertEqual(self.client.post(self.url(experiment), {}, content_type="application/json").status_code, 405)

    def test_preview_requires_the_course_manage_permission(self):
        experiment = Experiment.objects.create(course=self.course, owner=self.teacher, name="未配置实验")
        outsider = User.objects.create_user(username="preview-outsider", role="teacher")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url(experiment)).status_code, 404)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.url(experiment)).status_code, 403)

    def test_incomplete_draft_returns_a_business_error_instead_of_fabricated_questions(self):
        experiment = Experiment.objects.create(course=self.course, owner=self.teacher, name="未配置实验")
        response = self.client.get(self.url(experiment))
        self.assertEqual(response.status_code, 409)
