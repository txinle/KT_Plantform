import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    ClassGroup,
    ClassStudentAnalyticsPolicy,
    Course,
    CourseEnrollment,
    User,
)
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.services import publish_experiment, save_participant_rules
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .teaching.student_projection import (
    build_student_experiment_projection,
    build_student_learning_projection,
    build_student_question_projection,
)


class StudentVisibilityProjectionTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="student-visibility-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="学生可见性课程",
            code="STUDENT-VISIBILITY",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="可见性实验班",
            academic_year="2026",
        )
        self.students = []
        for index in range(5):
            student = User.objects.create_user(
                username=f"VIS-S{index + 1:03d}",
                student_number=f"VIS-S{index + 1:03d}",
                display_name=f"可见性学生{index + 1}",
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
            self.students.append(student)
        self.enrollment = CourseEnrollment.objects.get(
            course=self.course,
            student=self.students[0],
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="学生可见性题库",
        )
        self.concept = Concept.objects.create(course=self.course, name="KVL")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="KVL 诊断题",
            correct_answer="B",
            explanation="闭合回路电压代数和为零。",
        )
        for key, text in (("A", "错误选项"), ("B", "正确选项")):
            QuestionOption.objects.create(
                question=self.question,
                key=key,
                text=text,
            )
        QuestionConcept.objects.create(
            question=self.question,
            concept=self.concept,
            is_primary=True,
        )
        self.experiments = {}
        for key, feedback_mode in (
            ("none", Experiment.Feedback.NONE),
            ("correctness", Experiment.Feedback.CORRECTNESS),
            ("full", Experiment.Feedback.FULL),
        ):
            self.experiments[key] = self._publish_and_submit(
                name=f"{key} 反馈实验",
                feedback_mode=feedback_mode,
                students=self.students,
            )
        self.experiments["small"] = self._publish_and_submit(
            name="四人可比实验",
            feedback_mode=Experiment.Feedback.CORRECTNESS,
            students=self.students[:4],
            excluded_students=self.students[4:],
        )

    def _publish_and_submit(
        self,
        *,
        name,
        feedback_mode,
        students,
        excluded_students=(),
        questions=None,
        leave_last_ungraded_for_first=False,
    ):
        questions = tuple(questions or (self.question,))
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name=name,
            feedback_mode=feedback_mode,
        )
        for display_order, question in enumerate(questions, start=1):
            ExperimentQuestion.objects.create(
                experiment=experiment,
                question=question,
                display_order=display_order,
            )
        save_participant_rules(
            experiment,
            [self.class_group],
            [],
            excluded_students,
        )
        version = publish_experiment(experiment, self.teacher)
        class_snapshot = version.class_snapshots.get(
            source_class_group=self.class_group
        )
        question_snapshots = tuple(version.question_snapshots.order_by("source_sequence", "pk"))
        for index, student in enumerate(students):
            session = ExperimentSession.objects.create(
                version=version,
                student=student,
                class_snapshot=class_snapshot,
                class_snapshot_name=class_snapshot.display_name,
                status=ExperimentSession.Status.COMPLETED,
                completed_at=timezone.now(),
            )
            for sequence, question_snapshot in enumerate(question_snapshots, start=1):
                assignment = StudentQuestionAssignment.objects.create(
                    session=session,
                    snapshot=question_snapshot,
                    item_instance=get_or_create_default_item_instance(question_snapshot),
                    assigned_sequence_index=sequence,
                )
                is_correct = index in {0, 3, 4}
                final_answer = "B" if is_correct else "A"
                attempt = Attempt.objects.create(
                    session=session,
                    assignment=assignment,
                    status=Attempt.Status.SUBMITTED,
                    submitted_at=timezone.now(),
                    first_to_submit_duration_ms=(index + 1) * 1000,
                    final_answer=final_answer,
                )
                submission = SubmissionEvent.objects.create(
                    attempt=attempt,
                    submission_token=uuid.uuid4(),
                    final_answer=final_answer,
                    server_received_at=timezone.now(),
                    committed_at=timezone.now(),
                )
                if leave_last_ungraded_for_first and index == 0 and sequence == len(question_snapshots):
                    continue
                GradeRecord.objects.create(
                    submission=submission,
                    version=1,
                    status=GradeRecord.Status.COMPLETED,
                    validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
                    is_correct=is_correct,
                    score=Decimal("1") if is_correct else Decimal("0"),
                    max_score=Decimal("1"),
                )
        return experiment

    def test_partial_grading_is_labeled_with_submitted_item_coverage(self):
        second_question = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SINGLE,
            text="第二道待人工复核题",
            correct_answer="B",
        )
        for key, text in (("A", "错误"), ("B", "正确")):
            QuestionOption.objects.create(
                question=second_question,
                key=key,
                text=text,
            )
        QuestionConcept.objects.create(
            question=second_question,
            concept=self.concept,
            is_primary=True,
        )
        partial = self._publish_and_submit(
            name="部分评分实验",
            feedback_mode=Experiment.Feedback.CORRECTNESS,
            students=self.students,
            questions=(self.question, second_question),
            leave_last_ungraded_for_first=True,
        )

        detail = build_student_experiment_projection(
            self.enrollment,
            experiment_id=partial.pk,
        ).to_dict()
        row = next(
            item
            for item in build_student_learning_projection(self.enrollment)["experiments"]
            if item.experiment_id == partial.pk
        )

        self.assertEqual(detail["score_rate"].percent_display, "100.0%")
        self.assertEqual(detail["grading_coverage"]["graded"], 1)
        self.assertEqual(detail["grading_coverage"]["submitted"], 2)
        self.assertTrue(detail["grading_coverage"]["is_partial"])
        self.assertEqual(row.grading_coverage.graded, 1)
        self.assertEqual(row.grading_coverage.submitted, 2)
        self.assertTrue(row.grading_coverage.is_partial)

        with override_settings(
            CLASS_CENTER_ENABLED=True,
            STUDENT_LEARNING_ANALYTICS_ENABLED=True,
        ):
            self.client.force_login(self.students[0])
            response = self.client.get(
                reverse(
                    "assessment:learning_experiment",
                    args=[self.course.pk, partial.pk],
                )
            )
        self.assertContains(response, "已评分 1 / 2 题")
        self.assertContains(response, "部分结果")

    def test_home_projection_intersects_feedback_policy_and_minimum_sample(self):
        projection = build_student_learning_projection(self.enrollment)
        data = projection.to_dict()

        self.assertEqual(
            set(data),
            {
                "generated_at",
                "course_id",
                "course_name",
                "course_code",
                "course_term",
                "class_group_name",
                "completion",
                "correct_rate",
                "score_rate",
                "score_grading_coverage",
                "comparisons",
                "experiments",
                "experiment_correct_rate_available",
                "experiment_score_rate_available",
                "experiment_rank_available",
                "kc_rows",
                "trend_rows",
                "trend_class_average_available",
            },
        )
        self.assertEqual(data["completion"]["completed"], 4)
        self.assertEqual(data["completion"]["eligible"], 4)
        self.assertEqual(data["correct_rate"].ratio, Decimal("1"))
        self.assertEqual(data["score_rate"].ratio, Decimal("1"))
        self.assertEqual(
            data["comparisons"]["correct_rate"]["class_average"].sample_size,
            5,
        )
        by_id = {row["experiment_id"]: row for row in data["experiments"]}
        none_row = by_id[self.experiments["none"].pk]
        self.assertNotIn("correct_rate", none_row)
        self.assertNotIn("score_rate", none_row)
        self.assertNotIn("rank", none_row)
        small_row = by_id[self.experiments["small"].pk]
        self.assertNotIn("rank", small_row)
        correctness_row = by_id[self.experiments["correctness"].pk]
        self.assertEqual(correctness_row["rank"]["position"], 1)
        self.assertEqual(correctness_row["rank"]["sample_size"], 5)
        self.assertEqual(len(data["kc_rows"]), 1)
        self.assertEqual(len(data["trend_rows"]), 3)

    def test_disabled_policy_fields_are_absent_from_entire_projection(self):
        policy = self.class_group.student_analytics_policy
        policy.show_scores = False
        policy.show_correct_rate = False
        policy.show_item_results = False
        policy.show_class_average = False
        policy.rank_mode = ClassStudentAnalyticsPolicy.RankMode.NONE
        policy.show_kc_rates = False
        policy.show_trend = False
        policy.save()

        data = build_student_learning_projection(self.enrollment).to_dict()
        serialized = str(data)

        self.assertNotIn("correct_rate", data)
        self.assertNotIn("score_rate", data)
        self.assertNotIn("comparisons", data)
        self.assertNotIn("kc_rows", data)
        self.assertNotIn("trend_rows", data)
        self.assertNotIn("score_grading_coverage", data)
        self.assertNotIn("experiment_correct_rate_available", data)
        self.assertNotIn("experiment_score_rate_available", data)
        self.assertNotIn("experiment_rank_available", data)
        self.assertNotIn("trend_class_average_available", data)
        for row in data["experiments"]:
            for hidden in ("correct_rate", "score_rate", "rank", "item_results"):
                self.assertNotIn(hidden, row)
        for forbidden in (
            "correct_answer",
            "explanation",
            "process",
            "event_id",
            "snapshot_id",
            "payload",
            "content_hash",
            "student_number",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_each_policy_field_controls_only_its_allow_list_branch(self):
        policy = self.class_group.student_analytics_policy
        full_experiment_id = self.experiments["full"].pk
        cases = (
            ("show_scores", False, "score_rate"),
            ("show_correct_rate", False, "correct_rate"),
            ("show_class_average", False, "comparisons"),
            ("show_kc_rates", False, "kc_rows"),
            ("show_trend", False, "trend_rows"),
        )
        defaults = {
            "show_scores": True,
            "show_correct_rate": True,
            "show_item_results": True,
            "show_class_average": True,
            "rank_mode": ClassStudentAnalyticsPolicy.RankMode.SELF,
            "show_kc_rates": True,
            "show_trend": True,
        }
        for field, disabled_value, hidden_key in cases:
            with self.subTest(field=field):
                for name, value in defaults.items():
                    setattr(policy, name, value)
                setattr(policy, field, disabled_value)
                policy.save()
                home = build_student_learning_projection(self.enrollment).to_dict()
                self.assertNotIn(hidden_key, home)

        for name, value in defaults.items():
            setattr(policy, name, value)
        policy.show_item_results = False
        policy.save()
        experiment = build_student_experiment_projection(
            self.enrollment,
            experiment_id=full_experiment_id,
        ).to_dict()
        self.assertNotIn("item_results", experiment)

        policy.show_item_results = True
        policy.rank_mode = ClassStudentAnalyticsPolicy.RankMode.NONE
        policy.save()
        experiment = build_student_experiment_projection(
            self.enrollment,
            experiment_id=full_experiment_id,
        ).to_dict()
        self.assertNotIn("rank", experiment)

    def test_experiment_and_question_projection_respect_feedback_modes(self):
        correctness = build_student_experiment_projection(
            self.enrollment,
            experiment_id=self.experiments["correctness"].pk,
        ).to_dict()
        full = build_student_experiment_projection(
            self.enrollment,
            experiment_id=self.experiments["full"].pk,
        ).to_dict()

        self.assertIn("item_results", correctness)
        self.assertIn("item_results", full)
        correctness_question_id = correctness["item_results"][0]["question_id"]
        full_question_id = full["item_results"][0]["question_id"]
        correctness_question = build_student_question_projection(
            self.enrollment,
            experiment_id=self.experiments["correctness"].pk,
            question_id=correctness_question_id,
        ).to_dict()
        full_question = build_student_question_projection(
            self.enrollment,
            experiment_id=self.experiments["full"].pk,
            question_id=full_question_id,
        ).to_dict()

        self.assertEqual(correctness_question["final_answer"], "B：正确选项")
        self.assertNotIn("correct_answer", correctness_question)
        self.assertNotIn("explanation", correctness_question)
        self.assertEqual(full_question["correct_answer"], "B：正确选项")
        self.assertEqual(full_question["explanation"], "闭合回路电压代数和为零。")
        for projection in (correctness_question, full_question):
            self.assertNotIn("process", projection)
            self.assertNotIn("first_answer", projection)

        with self.assertRaises(PermissionDenied):
            build_student_experiment_projection(
                self.enrollment,
                experiment_id=self.experiments["none"].pk,
                require_results=True,
            )

    def test_item_policy_forbidden_is_403_but_cross_scope_is_not_found(self):
        policy = self.class_group.student_analytics_policy
        policy.show_item_results = False
        policy.save(update_fields=["show_item_results"])
        experiment = self.experiments["full"]
        version = experiment.versions.get()
        question_id = version.question_snapshots.get().pk

        with self.assertRaises(PermissionDenied):
            build_student_question_projection(
                self.enrollment,
                experiment_id=experiment.pk,
                question_id=question_id,
            )
        with self.assertRaises(LookupError):
            build_student_question_projection(
                self.enrollment,
                experiment_id=experiment.pk,
                question_id=question_id + 999,
            )

    @override_settings(
        CLASS_CENTER_ENABLED=True,
        STUDENT_LEARNING_ANALYTICS_ENABLED=True,
    )
    def test_student_learning_pages_render_allow_list_without_peer_or_process_data(self):
        self.client.force_login(self.students[0])
        home_url = reverse("assessment:learning", args=[self.course.pk])
        experiment_url = reverse(
            "assessment:learning_experiment",
            args=[self.course.pk, self.experiments["full"].pk],
        )
        full_question = self.experiments["full"].versions.get().question_snapshots.get()
        question_url = reverse(
            "assessment:learning_question",
            args=[self.course.pk, self.experiments["full"].pk, full_question.pk],
        )

        course_page = self.client.get(
            reverse("assessment:course", args=[self.course.pk])
        )
        home = self.client.get(home_url)
        experiment = self.client.get(experiment_url)
        question = self.client.get(question_url)

        self.assertEqual(home.status_code, 200)
        self.assertContains(course_page, home_url)
        self.assertContains(home, "我的学习")
        self.assertContains(home, "本人 vs 班级")
        self.assertContains(home, experiment_url)
        self.assertEqual(experiment.status_code, 200)
        self.assertContains(experiment, question_url)
        self.assertEqual(question.status_code, 200)
        self.assertContains(question, "正确答案")
        self.assertContains(question, "闭合回路电压代数和为零")
        combined = "".join(
            response.content.decode() for response in (home, experiment, question)
        )
        for peer in self.students[1:]:
            self.assertNotIn(peer.shown_name, combined)
            self.assertNotIn(peer.student_number, combined)
        for forbidden in (
            "作答过程",
            "event_id",
            "snapshot_id",
            "payload",
            "content_hash",
            "完整排行榜",
        ):
            self.assertNotIn(forbidden, combined)

    @override_settings(
        CLASS_CENTER_ENABLED=True,
        STUDENT_LEARNING_ANALYTICS_ENABLED=True,
    )
    def test_student_result_routes_distinguish_forbidden_from_cross_scope(self):
        self.client.force_login(self.students[0])
        none_url = reverse(
            "assessment:learning_experiment",
            args=[self.course.pk, self.experiments["none"].pk],
        )
        full_question = self.experiments["full"].versions.get().question_snapshots.get()
        wrong_question_url = reverse(
            "assessment:learning_question",
            args=[
                self.course.pk,
                self.experiments["full"].pk,
                full_question.pk + 999,
            ],
        )

        forbidden = self.client.get(none_url)
        missing = self.client.get(wrong_question_url)

        self.assertEqual(forbidden.status_code, 403)
        self.assertContains(forbidden, "返回实验任务", status_code=403)
        self.assertEqual(missing.status_code, 404)

        policy = self.class_group.student_analytics_policy
        policy.show_item_results = False
        policy.save(update_fields=["show_item_results"])
        item_url = reverse(
            "assessment:learning_question",
            args=[self.course.pk, self.experiments["full"].pk, full_question.pk],
        )
        item_forbidden = self.client.get(item_url)
        self.assertEqual(item_forbidden.status_code, 403)
        self.assertContains(item_forbidden, "返回实验任务", status_code=403)

    @override_settings(
        CLASS_CENTER_ENABLED=True,
        STUDENT_LEARNING_ANALYTICS_ENABLED=True,
    )
    def test_learning_route_requires_own_active_enrollment_but_allows_archived_history(self):
        url = reverse("assessment:learning", args=[self.course.pk])
        self.client.force_login(self.students[0])

        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.ARCHIVED
        )
        archived = self.client.get(url)
        self.assertEqual(archived.status_code, 200)
        self.assertContains(archived, "课程已归档")

        CourseEnrollment.objects.filter(pk=self.enrollment.pk).update(is_active=False)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.students[1])
        other_course = Course.objects.create(
            owner=self.teacher,
            name="未选课程",
            code="NOT-ENROLLED",
            term="2026 秋",
        )
        self.assertEqual(
            self.client.get(
                reverse("assessment:learning", args=[other_course.pk])
            ).status_code,
            404,
        )

    @override_settings(
        CLASS_CENTER_ENABLED=True,
        STUDENT_LEARNING_ANALYTICS_ENABLED=False,
    )
    def test_student_learning_flag_closes_routes_and_hides_course_navigation(self):
        self.client.force_login(self.students[0])
        learning_url = reverse("assessment:learning", args=[self.course.pk])

        self.assertEqual(self.client.get(learning_url).status_code, 404)
        course_page = self.client.get(
            reverse("assessment:course", args=[self.course.pk])
        )
        self.assertNotContains(course_page, learning_url)
