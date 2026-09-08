import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import (
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank


class TeacherDataCenterApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="data-api-teacher",
            role=User.Role.TEACHER,
            display_name="数据教师",
        )
        self.student = User.objects.create_user(
            username="data-api-student",
            role=User.Role.STUDENT,
            student_number="DATA-001",
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="数据 API 课程",
            code="DATA-API",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="实验班",
            academic_year="2026",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="数据题库",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="数据实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.class_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="数据题",
            correct_answer="答案",
        )
        snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=question,
            source_sequence=1,
            code="DATA-Q1",
            question_type=Question.Type.SHORT,
            text="数据题",
            correct_answer="答案",
        )
        session = ExperimentSession.objects.create(
            version=self.version,
            student=self.student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(session=session, assignment=assignment)
        self.client.force_login(self.teacher)

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def test_scope_and_overview_expose_operational_facts_and_safe_actions(self):
        scope = self.client.get(self.api("scope/"))
        overview = self.client.get(
            self.api("overview/"),
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
        )

        self.assertEqual(scope.status_code, 200)
        self.assertEqual(scope.json()["current"]["experiment_id"], None)
        self.assertEqual(scope.json()["experiments"][0]["name"], "数据实验")
        self.assertEqual(scope.json()["classes"][0]["name"], "实验班")
        self.assertNotIn("owner_id", scope.json()["course"])

        self.assertEqual(overview.status_code, 200)
        payload = overview.json()
        self.assertEqual(payload["facts"]["participants"], 1)
        self.assertEqual(payload["facts"]["observed_participants"], 1)
        self.assertEqual(payload["facts"]["completed_sessions"], 1)
        self.assertEqual(payload["actions"]["scope_update_url"], reverse(
            "courses:data_scope_update", args=[self.course.pk]
        ))
        self.assertNotIn("password", str(payload))

    def test_export_catalog_preserves_single_version_scope_and_confirmation_boundary(self):
        response = self.client.get(
            self.api("exports/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "package": "teaching",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_package"]["key"], "teaching")
        self.assertFalse(payload["selected_package"]["blocked"])
        self.assertEqual(payload["selected_export_version"]["version_number"], 1)
        self.assertEqual(payload["scope"]["values"]["version_id"], self.version.pk)
        self.assertEqual(
            payload["actions"]["create_url"],
            reverse("analytics:export_job_create", args=[self.course.pk]),
        )
        self.assertIn("实名", payload["identified_data_notice"])

    def test_quality_rows_are_paginated_and_keep_five_state_evidence(self):
        response = self.client.get(
            self.api("quality/"),
            {"experiment_id": self.experiment.pk, "page": 1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["size"], 20)
        self.assertIn("NOT_EVALUABLE", payload["counts"])
        self.assertTrue(payload["items"])
        self.assertEqual(
            set(payload["items"][0]),
            {
                "rule",
                "rule_version",
                "state",
                "entity_id",
                "source_mode",
                "reason_code",
                "evidence_summary",
            },
        )

    def test_export_history_is_stably_paginated_at_twenty_with_document_actions(self):
        for index in range(21):
            snapshot = ExportSnapshot.objects.create(
                course=self.course,
                experiment=self.experiment,
                created_by=self.teacher,
            )
            ExportJob.objects.create(
                course=self.course,
                experiment=self.experiment,
                snapshot=snapshot,
                created_by=self.teacher,
                package=f"package-{index:02d}",
                status=ExportJob.Status.SUCCEEDED,
                artifact_path=f"exports/{index}.zip",
            )

        response = self.client.get(self.api("exports/history/"), {"page": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["total_items"], 21)
        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(payload["items"][0]["package"], "package-20")
        self.assertTrue(payload["items"][0]["download_url"].endswith("/download/"))
        self.assertTrue(payload["items"][0]["delete_url"].endswith("/delete/"))

    def test_advanced_preview_is_one_student_per_row_searchable_and_business_only(self):
        self.student.display_name = "张同学"
        self.student.save(update_fields=["display_name"])
        for index in range(21):
            student = User.objects.create_user(
                username=f"data-extra-{index:02d}",
                role=User.Role.STUDENT,
                student_number=f"DATA-{index + 2:03d}",
                display_name=f"学生 {index:02d}",
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
        response = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["students"]), 20)
        self.assertEqual(payload["page"]["size"], 20)
        self.assertEqual(payload["page"]["total_items"], 22)
        self.assertEqual(len({row["student_number"] for row in payload["students"]}), 20)
        self.assertEqual(
            set(payload["students"][0]),
            {
                "student_number",
                "display_name",
                "class_name",
                "experiment_count",
                "progress_label",
                "status_label",
                "detail_url",
                "detail_api_url",
            },
        )
        serialized = str(payload).lower()
        for forbidden in (
            "schema", "table", "path", "event_id", "attempt_id",
            "submission_id", "snapshot_id", "hash",
        ):
            self.assertNotIn(forbidden, serialized)

        searched = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "search": "张同学",
            },
        ).json()
        self.assertEqual([row["student_number"] for row in searched["students"]], ["DATA-001"])

    def test_inaccessible_course_is_hidden(self):
        other = User.objects.create_user(username="data-api-other", role=User.Role.TEACHER)
        hidden = Course.objects.create(owner=other, name="隐藏", code="HIDDEN", term="2026")

        self.assertEqual(
            self.client.get(f"/api/v1/teacher/courses/{hidden.pk}/data/overview/").status_code,
            404,
        )


@override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
class TeacherDataCenterVueShellTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="data-shell-teacher", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="数据 Shell 课程",
            code="DATA-SHELL",
            term="2026 秋",
        )
        self.client.force_login(self.teacher)
        self.urls = [
            reverse("courses:data", args=[self.course.pk]),
            reverse("courses:data_exports", args=[self.course.pk]),
            reverse("courses:data_quality", args=[self.course.pk]),
            reverse("courses:data_export_history", args=[self.course.pk]),
            reverse("courses:data_advanced", args=[self.course.pk]),
        ]

    def rollout_env(self, **values):
        defaults = {
            "VUE_TEACHER_DATA_CENTER_ENABLED": "0",
            "VUE_TEACHER_DATA_CENTER_COURSE_IDS": "",
            "VUE_TEACHER_DATA_CENTER_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict(os.environ, defaults)

    def test_flag_off_preserves_data_center_templates(self):
        with self.rollout_env():
            responses = [self.client.get(url) for url in self.urls]

        self.assertTemplateUsed(responses[0], "teacher/data_center/overview.html")
        self.assertTemplateUsed(responses[-1], "teacher/data_center/advanced.html")
        for response in responses:
            self.assertNotContains(response, 'id="kt-app"')

    def test_pilot_course_receives_vue_shell_on_existing_read_routes(self):
        with self.rollout_env(
            VUE_TEACHER_DATA_CENTER_ENABLED="1",
            VUE_TEACHER_DATA_CENTER_COURSE_IDS=str(self.course.pk),
        ):
            responses = [self.client.get(url) for url in self.urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="kt-app"')
            self.assertContains(response, 'data-module="teacher-data-center"')

    def test_scope_post_remains_the_existing_document_flow(self):
        target = reverse("courses:data", args=[self.course.pk])
        with self.rollout_env(VUE_TEACHER_DATA_CENTER_ENABLED="1"):
            response = self.client.post(
                reverse("courses:data_scope_update", args=[self.course.pk]),
                {"experiment_id": "", "class_id": "", "next": target},
            )

        self.assertRedirects(response, target, fetch_redirect_response=False)
