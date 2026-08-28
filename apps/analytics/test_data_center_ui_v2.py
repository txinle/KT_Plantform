from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import (
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank
from apps.analytics.export_catalog import RESPONSE_EVENT_TYPES


class DataCenterUiV2Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="data-ui-teacher", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="data-ui-student", role=User.Role.STUDENT, student_number="DATA-UI-1"
        )
        self.course = Course.objects.create(
            owner=self.teacher, name="数据中心课程", code="DC101", term="2026 秋"
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="认知追踪实验班",
            academic_year="2026",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.not_participated = User.objects.create_user(
            username="data-ui-no-session",
            role=User.Role.STUDENT,
            student_number="DATA-UI-2",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.not_participated,
        )
        bank = QuestionBank.objects.create(
            course=self.course, created_by=self.teacher, name="数据中心题库"
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="数据中心实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment, version_number=1, published_by=self.teacher
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
            primary_kc="数据 KC",
            all_kcs=["数据 KC"],
        )
        session = ExperimentSession.objects.create(
            version=self.version,
            student=self.student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(session=session, assignment=assignment)
        self.client.force_login(self.teacher)

    def test_overview_exposes_only_four_operational_facts(self):
        response = self.client.get(reverse("courses:data", args=[self.course.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "参与学生")
        self.assertContains(response, "完成会话")
        self.assertContains(response, "已提交作答")
        self.assertContains(response, "质量告警")
        self.assertNotContains(response, "assessment_events.csv")

    def test_legacy_table_link_redirects_to_advanced_tools(self):
        response = self.client.get(
            reverse("courses:data", args=[self.course.pk]),
            {"table": "attempts", "experiment_id": self.experiment.pk},
        )

        self.assertRedirects(
            response,
            f"{reverse('courses:data_advanced', args=[self.course.pk])}?table=attempts&experiment_id={self.experiment.pk}",
            fetch_redirect_response=False,
        )

    def test_teaching_uses_experiment_and_class_scope_and_keeps_nonparticipants(self):
        response = self.client.get(
            reverse("courses:data_teaching", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "学生数据")
        self.assertContains(response, self.student.student_number)
        self.assertContains(response, self.not_participated.student_number)
        self.assertContains(response, "未参与")

    def test_ordinary_scope_form_exposes_only_experiment_and_class(self):
        response = self.client.get(reverse("courses:data", args=[self.course.pk]))

        self.assertContains(response, 'name="experiment_id"')
        self.assertContains(response, 'name="class_id"')
        for forbidden_name in (
            "version_id",
            "student_id",
            "session_id",
            "condition_id",
            "session_status",
            "attempt_status",
            "started_from_utc",
            "started_before_utc",
        ):
            self.assertNotContains(response, f'name="{forbidden_name}"')
        self.assertNotContains(response, "Version 1")

    def test_quality_history_export_and_advanced_have_dedicated_routes(self):
        for name, expected in (
            ("courses:data_exports", "导出数据包"),
            ("courses:data_quality", "数据质量"),
            ("courses:data_export_history", "导出记录"),
            ("courses:data_advanced", "高级数据工具"),
        ):
            response = self.client.get(reverse(name, args=[self.course.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)

    def test_scope_is_remembered_only_after_post_confirmation(self):
        response = self.client.post(
            reverse("courses:data_scope_update", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
                "next": reverse("courses:data_teaching", args=[self.course.pk]),
            },
        )
        self.assertRedirects(
            response,
            reverse("courses:data_teaching", args=[self.course.pk]),
            fetch_redirect_response=False,
        )
        remembered = self.client.get(reverse("courses:data_teaching", args=[self.course.pk]))
        self.assertContains(remembered, self.experiment.name)
        self.assertContains(remembered, self.class_group.name)
        self.assertNotContains(remembered, "Version 1")

    def test_export_requires_scope_and_identified_data_confirmation(self):
        export_page = self.client.get(
            reverse("courses:data_exports", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "package": "teaching",
            },
        )
        self.assertContains(export_page, "确认生成实名数据包")
        rejected = self.client.post(
            reverse("analytics:export_job_create", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "package": "teaching",
            },
        )
        self.assertEqual(rejected.status_code, 400)

    def test_advanced_tools_use_six_semantic_groups_and_one_open_group(self):
        response = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {"experiment_id": self.experiment.pk, "table": "assessment_events"},
        )

        self.assertEqual(response.status_code, 200)
        for label in (
            "实验与参与者",
            "题目与知识结构",
            "作答与评分",
            "行为过程",
            "扩展观测",
            "派生研究数据",
        ):
            self.assertContains(response, label)
        self.assertContains(response, 'data-advanced-group-open="true"', count=1)

    def test_advanced_catalog_marks_event_authority_and_derived_q_matrix(self):
        response = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {"experiment_id": self.experiment.pk, "table": "assessment_events"},
        )

        items = {
            item["key"]: (group["key"], item)
            for group in response.context["advanced_groups"]
            for item in group["tables"]
        }
        self.assertEqual(items["assessment_events"][1]["role"], "source_of_truth")
        self.assertEqual(items["response_events"][1]["role"], "classified_view")
        self.assertEqual(items["navigation_events"][1]["role"], "classified_view")
        self.assertEqual(items["telemetry_events"][1]["role"], "classified_view")
        self.assertEqual(items["q_matrix"][0], "derived")

    def test_advanced_sidebar_uses_logical_names_and_keeps_path_in_technical_info(self):
        response = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {"experiment_id": self.experiment.pk, "table": "assessment_events"},
        )

        html = response.content.decode()
        sidebar = html.split('data-advanced-sidebar="true"', 1)[1].split(
            'data-advanced-content="true"', 1
        )[0]
        self.assertIn("assessment_events", sidebar)
        self.assertNotIn("03_raw_events/assessment_events.csv", sidebar)
        self.assertContains(response, "技术信息")
        self.assertContains(response, "03_raw_events/assessment_events.csv")

    def test_advanced_extensions_are_hidden_until_enabled_for_experiment(self):
        url = reverse("courses:data_advanced", args=[self.course.pk])
        hidden = self.client.get(url, {"experiment_id": self.experiment.pk})
        self.assertNotContains(hidden, "Confidence信心")
        self.assertNotContains(hidden, "仿真事件")

        self.experiment.confidence_mode = Experiment.ConfidenceMode.ALL
        self.experiment.save(update_fields=["confidence_mode"])
        self.version.configuration = {"simulation_enabled": True}
        self.version.save(update_fields=["configuration"])
        enabled = self.client.get(url, {"experiment_id": self.experiment.pk})
        self.assertContains(enabled, "Confidence信心")
        self.assertContains(enabled, "仿真事件")
        self.assertContains(enabled, "仿真快照")

    def test_research_scope_survives_advanced_table_and_export_package_links(self):
        research_scope = {
            "experiment_id": self.experiment.pk,
            "version_mode": "single",
            "version_id": self.version.pk,
            "class_snapshot_id": self.class_snapshot.pk,
            "student_id": self.student.pk,
            "session_id": self.attempt.session_id,
        }
        advanced = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {**research_scope, "table": "assessment_events"},
        )
        export_page = self.client.get(
            reverse("courses:data_exports", args=[self.course.pk]), research_scope
        )

        for response in (advanced, export_page):
            self.assertEqual(response.status_code, 200)
            query = response.context["scope_query"]
            for key, value in research_scope.items():
                self.assertIn(f"{key}={value}", query)

        navigation = {item["key"]: item["url"] for item in advanced.context["data_navigation"]}
        for target in ("advanced", "exports", "quality"):
            for key, value in research_scope.items():
                self.assertIn(f"{key}={value}", navigation[target])
        self.assertNotIn("student_id", navigation["teaching"])
        self.assertNotIn("session_id", navigation["overview"])

    def test_advanced_invalid_page_falls_back_to_first_page(self):
        response = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "table": "attempts",
                "page": "not-a-number",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["preview"]["page"], 1)

    def test_disabled_extension_exports_are_hidden_until_enabled(self):
        url = reverse("courses:data_exports", args=[self.course.pk])
        hidden = self.client.get(url, {"experiment_id": self.experiment.pk})
        self.assertNotContains(hidden, "Confidence")
        self.assertNotContains(hidden, "Simulation")
        rejected = self.client.post(
            reverse("analytics:export_job_create", args=[self.course.pk]),
            {
                "experiment_id": self.experiment.pk,
                "package": "confidence",
                "confirm_identified": "yes",
            },
        )
        self.assertEqual(rejected.status_code, 400)

        self.experiment.confidence_mode = Experiment.ConfidenceMode.ALL
        self.experiment.save(update_fields=["confidence_mode"])
        self.version.configuration = {"simulation_enabled": True}
        self.version.save(update_fields=["configuration"])
        enabled = self.client.get(url, {"experiment_id": self.experiment.pk})
        self.assertContains(enabled, "Confidence")
        self.assertContains(enabled, "Simulation")

    def test_text_edit_is_in_response_view_and_stable_observation_is_a_projection(self):
        self.assertIn("text_edit_observed", RESPONSE_EVENT_TYPES)
        response = self.client.get(
            reverse("courses:data_advanced", args=[self.course.pk]),
            {"experiment_id": self.experiment.pk},
        )
        items = {
            item["key"]: item
            for group in response.context["advanced_groups"]
            for item in group["tables"]
        }
        self.assertEqual(
            items["response_input_observations"]["role"], "classified_view"
        )
