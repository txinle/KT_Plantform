import os
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.analytics.models import ExportJob, ExportSnapshot, ResearchSessionMaterialization
from apps.analytics.research_materialization import PROJECTION_VERSION
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
        self.session = session
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

    def test_overview_accepts_click_selected_experiment_ids_and_preserves_multi_scope(self):
        other = Experiment.objects.create(course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="另一实验")
        for ids in [str(self.experiment.pk), f"{self.experiment.pk},{other.pk}"]:
            with self.subTest(ids=ids):
                response = self.client.get(self.api("overview/"),
                    {"experiment_ids": ids, "class_id": self.class_group.pk})
                self.assertEqual(response.status_code, 200, response.content[:200])
                self.assertTrue(response.json()["scope"]["is_complete"])
                self.assertEqual(response.json()["facts"]["total_sessions"], 1)
                self.assertEqual(response.json()["facts"]["participants"], 1)
                if "," in ids:
                    self.assertIn("另一实验", response.json()["scope"]["display"])

    @override_settings(
        ADVANCED_DATA_MATERIALIZED_ENABLED=True,
        ADVANCED_DATA_MATERIALIZED_COURSE_IDS=("*",),
    )
    @patch("apps.analytics.api.views.build_quality_report")
    def test_overview_uses_materialized_process_and_quality_counts(self, build_quality_report):
        build_quality_report.side_effect = AssertionError(
            "materialized overview must not build the legacy quality report"
        )
        ResearchSessionMaterialization.objects.create(
            session=self.session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            row_counts={"responses": 1, "visits": 2, "behavior": 17},
            quality_rows=[
                {"attempt_id": self.attempt.pk, "state": "WARN"},
                {"attempt_id": self.attempt.pk, "state": "FAIL"},
                {"attempt_id": self.attempt.pk, "state": "NOT_EVALUABLE"},
            ],
        )

        response = self.client.get(
            self.api("overview/"),
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
        )

        self.assertEqual(response.status_code, 200)
        build_quality_report.assert_not_called()
        self.assertEqual(response.json()["facts"]["process_records"], 17)
        self.assertEqual(response.json()["facts"]["quality_warnings"], 1)
        self.assertEqual(response.json()["facts"]["not_evaluable"], 1)

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
                "experiment_ids": [self.experiment.pk],
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

    def test_deleted_saved_research_scope_resets_and_allows_reselection(self):
        deleted = Experiment.objects.create(course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="已删除的筛选实验")
        deleted_id = deleted.pk
        deleted.delete()
        research_key = f"data_center_scope_v1:{self.course.pk}"
        teaching_key = f"data_center_teaching_scope_v1:{self.course.pk}"
        session = self.client.session
        session[research_key] = {"experiment_ids": [self.experiment.pk, deleted_id], "class_id": self.class_group.pk}
        session[teaching_key] = {"experiment_id": deleted_id, "class_id": self.class_group.pk}
        session["data_center_scope_v1:99999"] = {"experiment_id": 123}
        session.save()

        response = self.client.get(self.api("scope/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current"]["experiment_ids"], [])
        self.assertIsNone(response.json()["current"]["class_id"])
        self.assertFalse(response.json()["current"]["is_complete"])
        self.assertNotIn(research_key, self.client.session)
        self.assertNotIn(teaching_key, self.client.session)
        self.assertEqual(self.client.session["data_center_scope_v1:99999"], {"experiment_id": 123})
        self.assertEqual(response.json()["experiments"][0]["id"], self.experiment.pk)
        self.assertEqual(self.client.get(self.api("overview/")).status_code, 200)
        selected = self.client.post(self.api("scope/"), {
            "experiment_ids": [self.experiment.pk], "class_id": self.class_group.pk,
        }, content_type="application/json")
        self.assertEqual(selected.status_code, 200)
        self.assertTrue(selected.json()["current"]["is_complete"])

    def test_invalid_saved_scope_formats_and_teaching_fallback_reset(self):
        for prefix, values in [
            ("data_center_scope_v1", {"experiment_ids": [self.experiment.pk], "class_id": 999999}),
            ("data_center_scope_v1", {"experiment_ids": "invalid"}),
            ("data_center_teaching_scope_v1", {"experiment_id": 999999}),
        ]:
            with self.subTest(prefix=prefix, values=values):
                key = f"{prefix}:{self.course.pk}"
                session = self.client.session
                session[key] = values
                session.save()
                response = self.client.get(self.api("scope/"))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["current"]["is_complete"])
                self.assertNotIn(key, self.client.session)

    def test_explicit_invalid_scope_remains_rejected_and_preserves_saved_selection(self):
        key = f"data_center_scope_v1:{self.course.pk}"
        saved = {"experiment_ids": [self.experiment.pk], "class_id": self.class_group.pk}
        session = self.client.session
        session[key] = saved
        session.save()
        for query, status in [({"experiment_ids": "999999"}, 404), ({"experiment_ids": "invalid"}, 400)]:
            with self.subTest(query=query):
                response = self.client.get(self.api("scope/"), query)
                self.assertEqual(response.status_code, status)
                self.assertEqual(self.client.session[key], saved)

    def test_explicit_student_scope_is_checked_after_stale_saved_base_resets(self):
        key = f"data_center_scope_v1:{self.course.pk}"
        session = self.client.session
        session[key] = {"experiment_ids": [999999], "class_id": self.class_group.pk}
        session.save()
        response = self.client.get(self.api("scope/"), {"student_id": self.student.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current"]["experiment_ids"], [])
        self.assertNotIn(key, self.client.session)
        self.assertEqual(self.client.get(self.api("scope/"), {"student_id": 999999}).status_code, 404)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_stale_teaching_scope_returns_to_class_selection(self):
        key = f"data_center_teaching_scope_v1:{self.course.pk}"
        session = self.client.session
        session[key] = {"experiment_id": 999999, "class_id": self.class_group.pk}
        session.save()
        url = reverse("courses:data_teaching", args=[self.course.pk])
        response = self.client.get(url)
        self.assertRedirects(response, reverse("courses:classes", args=[self.course.pk]), fetch_redirect_response=False)
        self.assertNotIn(key, self.client.session)
        self.assertEqual(self.client.get(url, {"experiment_id": 999999}).status_code, 404)

    def test_export_job_create_queues_background_work_and_artifact_removal_uses_json(self):
        create_url = self.api("export-jobs/")
        payload = {
            "package": "teaching",
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
        self.assertEqual(job.status, ExportJob.Status.QUEUED)
        self.assertFalse(job.artifact_path)
        self.assertEqual(job.snapshot.filter_definition["version_id"], self.version.pk)
        call_command("run_export_jobs", once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, ExportJob.Status.SUCCEEDED)
        self.assertTrue(job.artifact_path)
        removed = self.client.delete(
            self.api(f"export-jobs/{job.job_id}/"),
            content_type="application/json",
        )
        self.assertEqual(removed.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.artifact_removal_reason, "user_requested_removal")

    def test_export_catalog_uses_single_experiment_v2_scope(self):
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
        self.assertEqual(payload["selected_package"]["key"], "experiment")
        self.assertFalse(payload["selected_package"]["blocked"])
        self.assertIsNone(payload["selected_export_version"])
        self.assertEqual(
            payload["scope"]["values"],
            {
                "scope_version": 2,
                "experiment_id": self.experiment.pk,
                "class_mode": "single",
                "class_id": self.class_group.pk,
            },
        )
        self.assertEqual(
            payload["actions"]["create_url"],
            reverse("teacher-data-center-export-job-create", args=[self.course.pk]),
        )
        self.assertIn("实名", payload["identified_data_notice"])

    def test_quality_rows_are_paginated_and_keep_five_state_evidence(self):
        response = self.client.get(
            self.api("quality/"),
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk, "page": 1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["size"], 20)
        self.assertIn("无法评估", payload["counts"])
        self.assertTrue(payload["items"])
        self.assertEqual(
            set(payload["items"][0]),
            {
                "student_number", "student_name", "session_ref", "question_code",
                "state", "state_label", "problem", "impact",
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

    def test_research_catalog_has_six_tables_and_filters_preview_by_student_name(self):
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
        self.assertEqual([table["key"] for table in payload["tables"]], [
            "sessions", "questions", "knowledge_points", "question_kc_mapping", "responses", "behavior_events",
        ])
        self.assertEqual(payload["search"], "张同学")
        self.assertEqual(payload["selected_table"]["key"], "responses")
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        row = dict(zip(
            [column["name"] for column in payload["preview"]["columns"]],
            payload["preview"]["rows"][0],
        ))
        self.assertEqual(row["姓名"], "张同学")
        self.assertEqual(row["学号"], "DATA-001")
        self.assertEqual(row["课程内题目ID"], "Q001")
        self.assertNotIn("attempt_id", row)

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

    def test_research_catalog_projects_readable_behavior_with_paginated_preview(self):
        response = self.client.get(
            self.api("advanced/"),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "table": "behavior_events",
                "page": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["tables"]), 6)
        self.assertEqual(payload["selected_table"]["key"], "behavior_events")
        self.assertEqual(payload["selected_table"]["path"], "行为过程.csv")
        self.assertNotIn("role", payload["selected_table"])
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
