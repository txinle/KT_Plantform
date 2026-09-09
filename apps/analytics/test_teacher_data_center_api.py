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
            "teacher-data-center-scope", args=[self.course.pk]
        ))
        self.assertNotIn("password", str(payload))

    def test_scope_update_is_json_and_persists_the_authoritative_selection(self):
        url = self.api("scope/")

        updated = self.client.post(
            url,
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
            },
            content_type="application/json",
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json()["current"],
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "is_complete": True,
                "display": "数据实验 · 实验班",
            },
        )
        self.assertEqual(self.client.get(url).json()["current"]["class_id"], self.class_group.pk)

    def test_scope_update_rejects_cross_course_values_without_replacing_the_saved_scope(self):
        url = self.api("scope/")
        self.client.post(
            url,
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
            content_type="application/json",
        )
        other = User.objects.create_user(username="data-other-owner", role=User.Role.TEACHER)
        other_course = Course.objects.create(owner=other, name="其他数据课程", code="DATA-OTHER", term="2026")
        other_class = ClassGroup.objects.create(course=other_course, name="越界班", academic_year="2026")

        rejected = self.client.post(
            url,
            {"experiment_id": self.experiment.pk, "class_id": other_class.pk},
            content_type="application/json",
        )

        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(self.client.get(url).json()["current"]["class_id"], self.class_group.pk)

    def test_export_job_create_and_artifact_removal_use_json_contracts(self):
        create_url = self.api("export-jobs/")
        payload = {
            "package": "complete",
            "confirm_identified": True,
            "scope": {
                "experiment_id": self.experiment.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "class_snapshot_id": self.class_snapshot.pk,
            },
        }

        unconfirmed = self.client.post(
            create_url,
            {**payload, "confirm_identified": False},
            content_type="application/json",
        )
        created = self.client.post(create_url, payload, content_type="application/json")

        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(created.status_code, 201)
        job = ExportJob.objects.get(job_id=created.json()["job_id"])
        self.assertEqual(job.status, ExportJob.Status.SUCCEEDED)
        self.assertEqual(job.snapshot.filter_definition["version_id"], self.version.pk)
        removed = self.client.delete(
            self.api(f"export-jobs/{job.job_id}/"),
            content_type="application/json",
        )
        self.assertEqual(removed.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.artifact_removal_reason, "user_requested_removal")

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
            reverse("teacher-data-center-export-job-create", args=[self.course.pk]),
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
        self.assertTrue(
            payload["items"][0]["delete_url"].endswith(
                f"/export-jobs/{payload['items'][0]['job_id']}/"
            )
        )

    def test_advanced_catalog_keeps_all_groups_and_filters_preview_by_student_name(self):
        self.student.display_name = "张同学"
        self.student.save(update_fields=["display_name"])
        response = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "table": "attempts",
                "search": "张同学",
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [group["key"] for group in payload["groups"]],
            [
                "experiment_participants",
                "questions_knowledge",
                "responses_grading",
                "behavior_process",
                "extensions",
                "derived",
            ],
        )
        self.assertEqual(payload["search"], "张同学")
        self.assertEqual(payload["selected_table"]["key"], "attempts")
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        self.assertIn("student_name", [column["name"] for column in payload["preview"]["columns"]])
        self.assertIn("attempt_id", [column["name"] for column in payload["preview"]["columns"]])
        row = dict(zip(
            [column["name"] for column in payload["preview"]["columns"]],
            payload["preview"]["rows"][0],
        ))
        self.assertEqual(row["student_name"], "张同学")
        self.assertEqual(row["student_number"], "DATA-001")
        self.assertEqual(row["question_code"], "DATA-Q1")
        self.assertEqual(row["attempt_id"], self.attempt.pk)

        missing = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "table": "attempts",
                "search": "不存在的学生",
            },
        ).json()
        self.assertEqual(missing["preview"]["page"]["total_items"], 0)

    def test_advanced_catalog_preserves_event_authority_and_paginated_table_preview(self):
        response = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "table": "assessment_events",
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["groups"]), 6)
        self.assertEqual(
            [group["key"] for group in payload["groups"]],
            [
                "experiment_participants",
                "questions_knowledge",
                "responses_grading",
                "behavior_process",
                "extensions",
                "derived",
            ],
        )
        self.assertEqual(payload["selected_table"]["key"], "assessment_events")
        self.assertEqual(payload["selected_table"]["role"], "source_of_truth")
        self.assertEqual(
            payload["selected_table"]["path"],
            "03_raw_events/assessment_events.csv",
        )
        self.assertEqual(payload["preview"]["page"]["size"], 20)
        self.assertIn("columns", payload["preview"])
        self.assertIn("rows", payload["preview"])

    def test_inaccessible_course_is_hidden(self):
        other = User.objects.create_user(username="data-api-other", role=User.Role.TEACHER)
        hidden = Course.objects.create(owner=other, name="隐藏", code="HIDDEN", term="2026")

        self.assertEqual(
            self.client.get(f"/api/v1/teacher/courses/{hidden.pk}/data/overview/").status_code,
            404,
        )


@override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
class TeacherDataCenterFrontendEntryTests(TestCase):
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

    def test_all_data_center_pages_use_the_independent_frontend(self):
        with patch.dict(os.environ, {"FRONTEND_INTERNAL_REDIRECT": "/__frontend/index.html"}):
            responses = [self.client.get(url) for url in self.urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Accel-Redirect"], "/__frontend/index.html")
            self.assertFalse(response.templates)

    def test_legacy_document_scope_post_is_retired(self):
        response = self.client.post(
            reverse("courses:data_scope_update", args=[self.course.pk]),
            {"experiment_id": "", "class_id": ""},
        )

        self.assertEqual(response.status_code, 405)
