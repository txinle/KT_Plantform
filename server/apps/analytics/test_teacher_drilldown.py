from apps.api.test_support import domain_payload
import uuid
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.event_services import append_assessment_event
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.services import (
    _publish_legacy_experiment as publish_experiment,
    save_participant_rules,
)
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .models import RecordingCoverageAttempt, RecordingCoverageRun
from .teaching.drilldown import (
    TransitionApplicability,
    build_attempt_process_detail,
    build_question_detail,
    build_student_detail,
    build_student_experiment_detail,
)
from .teaching.process import ProcessCompleteness
from .teaching.kc_time import summarize_question_timing


class QuestionTimingSummaryTests(TransactionTestCase):
    def test_median_iqr_exclude_wall_clock_fallback_and_report_quality_composition(self):
        now = timezone.now()
        attempts = tuple(
            SimpleNamespace(pk=index, first_opened_at=now - timedelta(seconds=90))
            for index in range(1, 6)
        )
        submissions = {
            attempt.pk: SimpleNamespace(committed_at=now) for attempt in attempts
        }

        def heartbeat_pair(duration_ms, *, synchronized=False):
            instance = uuid.uuid4()
            origin = now - timedelta(minutes=1)
            first_estimate = origin if synchronized else None
            second_estimate = (
                origin + timedelta(milliseconds=duration_ms)
                if synchronized
                else None
            )
            return (
                SimpleNamespace(
                    event_type="activity_heartbeat",
                    client_instance_id=instance,
                    client_time_origin=origin,
                    client_monotonic_ms=Decimal("0"),
                    estimated_occurred_at_utc=first_estimate,
                    server_received_at=origin,
                ),
                SimpleNamespace(
                    event_type="activity_heartbeat",
                    client_instance_id=(uuid.uuid4() if synchronized else instance),
                    client_time_origin=(None if synchronized else origin),
                    client_monotonic_ms=Decimal(duration_ms),
                    estimated_occurred_at_utc=second_estimate,
                    server_received_at=origin + timedelta(milliseconds=duration_ms),
                ),
            )

        events = {
            1: heartbeat_pair(10_000),
            2: heartbeat_pair(20_000, synchronized=True),
            3: heartbeat_pair(30_000),
            4: heartbeat_pair(40_000, synchronized=True),
            5: (),
        }

        summary = summarize_question_timing(
            generated_at=now,
            attempts=attempts,
            submissions_by_attempt=submissions,
            events_by_attempt=events,
        )

        self.assertEqual(summary.sample_size, 4)
        self.assertEqual(summary.median_ms, Decimal("25000"))
        self.assertEqual(summary.q1_ms, Decimal("17500"))
        self.assertEqual(summary.q3_ms, Decimal("32500"))
        self.assertEqual(summary.quality.exact_count, 2)
        self.assertEqual(summary.quality.estimated_count, 2)
        self.assertEqual(summary.quality.wall_clock_count, 1)
        self.assertEqual(summary.quality.unavailable_count, 0)
        self.assertEqual(summary.quality.submitted_count, 5)


class TeacherQuestionDrilldownTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher-drilldown",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="教师深钻课程",
            code="DRILL-101",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="深钻实验班",
            academic_year="2026",
        )
        self.students = {
            key: self._enroll(f"DRILL-{key}", f"学生 {key}")
            for key in ("A", "B", "C", "D")
        }
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="深钻题库",
        )
        concept = Concept.objects.create(course=self.course, name="KVL")
        single = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="KVL 单选诊断",
            correct_answer="B",
        )
        multiple = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.MULTIPLE,
            text="KVL 多选诊断",
            correct_answer=["A", "B"],
        )
        for question in (single, multiple):
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
            for order, key in enumerate(("A", "B", "C"), start=1):
                QuestionOption.objects.create(
                    question=question,
                    key=key,
                    text=f"选项 {key}",
                    display_order=order,
                )
        self.single_source_question = single
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="首答终答实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=single,
            display_order=1,
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=multiple,
            display_order=2,
        )
        save_participant_rules(self.experiment, [self.class_group], [], [])
        self.version = publish_experiment(self.experiment, self.teacher)
        self.class_snapshot = self.version.class_snapshots.get(
            source_class_group=self.class_group
        )
        snapshots = list(self.version.question_snapshots.order_by("source_sequence"))
        self.single_snapshot, self.multiple_snapshot = snapshots
        paths = {
            "A": (["A", "B"], "B", True),
            "B": (["B", "A"], "A", False),
            "C": (["A"], "A", False),
            "D": (["B"], "B", True),
        }
        self.attempts = {}
        for key, (states, final_answer, is_correct) in paths.items():
            session = ExperimentSession.objects.create(
                version=self.version,
                student=self.students[key],
                class_snapshot=self.class_snapshot,
                class_snapshot_name=self.class_snapshot.display_name,
                status=ExperimentSession.Status.COMPLETED,
                completed_at=timezone.now(),
            )
            self.attempts[(key, "single")] = self._submit_choice(
                session,
                self.single_snapshot,
                sequence=1,
                states=states,
                final_answer=final_answer,
                is_correct=is_correct,
            )
            self.attempts[(key, "multiple")] = self._submit_choice(
                session,
                self.multiple_snapshot,
                sequence=2,
                states=[["A"], ["A", "B"]],
                final_answer=["A", "B"],
                is_correct=True,
            )
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.students["C"],
        ).update(is_active=False)

    def _enroll(self, student_number, display_name):
        student = User.objects.create_user(
            username=student_number,
            student_number=student_number,
            display_name=display_name,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=student,
        )
        return student

    def _submit_choice(
        self,
        session,
        snapshot,
        *,
        sequence,
        states,
        final_answer,
        is_correct,
    ):
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=sequence,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            submitted_at=timezone.now(),
            first_to_submit_duration_ms=sequence * 1000,
            final_answer=final_answer,
        )
        client_id = uuid.uuid4()
        client_time_origin = timezone.now()
        before = None
        for client_sequence, after in enumerate(states, start=1):
            append_assessment_event(
                event_type="response_state_changed",
                event_version=1,
                event_domain="assessment",
                actor_source="client",
                session=session,
                attempt=attempt,
                client_instance_id=client_id,
                client_sequence_no=client_sequence,
                client_monotonic_ms=client_sequence * 100,
                client_time_origin=client_time_origin,
                payload={
                    "question_type": snapshot.question_type,
                    "response_state_before": before,
                    "response_state_after": after,
                    "change_source": "student",
                },
            )
            before = after
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer=final_answer,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=is_correct,
            score=Decimal("1") if is_correct else Decimal("0"),
            max_score=Decimal("1"),
        )
        return attempt

    def test_single_choice_detail_uses_frozen_cohort_and_four_fact_transitions(self):
        detail = build_question_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            question_id=self.single_snapshot.pk,
        )

        self.assertEqual(detail.eligible_count, 4)
        self.assertEqual(detail.current_member_count, 3)
        self.assertEqual(detail.historical_participant_count, 1)
        self.assertEqual(detail.answer_count, 4)
        self.assertEqual(detail.correct_rate.numerator, Decimal("2"))
        self.assertEqual(detail.correct_rate.denominator, Decimal("4"))
        self.assertEqual(
            {row.key: row.count for row in detail.option_rows},
            {"A": 2, "B": 2, "C": 0},
        )
        self.assertEqual(
            detail.transition_applicability,
            TransitionApplicability.APPLICABLE,
        )
        self.assertEqual(
            {row.key: row.count for row in detail.transition_rows},
            {
                "correct_to_correct": 1,
                "incorrect_to_correct": 1,
                "correct_to_incorrect": 1,
                "incorrect_to_incorrect": 1,
            },
        )
        self.assertEqual(detail.chain_coverage.numerator, Decimal("4"))
        self.assertEqual(detail.chain_coverage.denominator, Decimal("4"))
        participants = {row.student_number: row for row in detail.participants}
        self.assertFalse(participants["DRILL-C"].is_current_member)
        serialized = str(asdict(detail))
        for forbidden in (
            "event_id",
            "snapshot_id",
            "client_instance_id",
            "payload",
            "hash",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_multiple_choice_reports_distribution_but_not_forced_four_categories(self):
        detail = build_question_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            question_id=self.multiple_snapshot.pk,
        )

        self.assertEqual(
            detail.transition_applicability,
            TransitionApplicability.NOT_APPLICABLE,
        )
        self.assertEqual(detail.transition_rows, ())
        self.assertEqual(
            {row.key: row.count for row in detail.option_rows},
            {"A": 4, "B": 4, "C": 0},
        )

    def test_process_detail_accepts_historical_frozen_participant_without_roster_leak(self):
        detail = build_attempt_process_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            question_id=self.single_snapshot.pk,
            student_id=self.students["C"].pk,
        )

        self.assertFalse(detail.is_current_member)
        self.assertEqual(detail.student_number, "DRILL-C")
        self.assertEqual(
            detail.process.completeness,
            ProcessCompleteness.COMPLETE,
        )
        self.assertEqual(detail.process.first_value, "A")
        self.assertEqual(detail.process.final_value, "A")

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_process_page_projects_coverage_and_time_evidence_into_teacher_language(self):
        run = RecordingCoverageRun.objects.create(
            scope_hash="0" * 64,
            parameter_hash="1" * 64,
            source_hash="2" * 64,
        )
        RecordingCoverageAttempt.objects.create(
            run=run,
            attempt=self.attempts[("A", "single")],
            overall_status="partial",
            time_quality="exact_monotonic",
            missing_reasons=[
                "missing_item_left",
                "cross_instance_order_uncertain",
            ],
        )

        detail = build_attempt_process_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            question_id=self.single_snapshot.pk,
            student_id=self.students["A"].pk,
        )

        self.assertEqual(detail.coverage_status_title, "过程记录不完整")
        self.assertEqual(detail.coverage_time_title, "步骤间隔可可靠比较")
        self.assertEqual(detail.coverage_reason_labels[0], "未记录离开题目")
        self.assertTrue(detail.has_cross_instance_order_uncertainty)
        for raw_field in (
            "coverage_status",
            "coverage_time_quality",
            "coverage_missing_reasons",
            "coverage_run_id",
        ):
            self.assertFalse(hasattr(detail, raw_field))
        self.assertIn(
            "服务器接收时间；与上一步的间隔可可靠比较",
            [step.time_source for step in detail.process.steps],
        )
        self.assertNotIn(
            "exact_monotonic",
            [step.time_source for step in detail.process.steps],
        )

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_process_page_labels_question_visits_in_chinese(self):
        attempt = self.attempts[("A", "single")]
        for _ in range(2):
            append_assessment_event(
                event_type="item_entered",
                event_version=1,
                event_domain="assessment",
                actor_source="server",
                session=attempt.session,
                assignment=attempt.assignment,
                attempt=attempt,
                question_snapshot=attempt.assignment.snapshot,
                item_instance=attempt.assignment.item_instance,
                payload={
                    "entry_reason": "navigation_click",
                    "from_assignment_id": None,
                },
            )

        detail = build_attempt_process_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            question_id=self.single_snapshot.pk,
            student_id=self.students["A"].pk,
        )
        labels = [step.label for step in detail.process.steps]

        self.assertIn("进入题目（第 1 次进入）", labels)
        self.assertIn("进入题目（第 2 次进入）", labels)
        self.assertFalse(any("Visit" in label for label in labels))

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_process_page_hides_navigation_assignment_ids_and_close_reason_codes(self):
        attempt = self.attempts[("A", "single")]
        destination = self.attempts[("A", "multiple")].assignment
        append_assessment_event(
            event_type="item_left",
            event_version=2,
            event_domain="assessment",
            actor_source="client",
            session=attempt.session,
            assignment=attempt.assignment,
            attempt=attempt,
            question_snapshot=attempt.assignment.snapshot,
            item_instance=attempt.assignment.item_instance,
            client_instance_id=uuid.uuid4(),
            client_sequence_no=1,
            payload={
                "leave_reason": "navigation",
                "to_assignment_id": destination.pk,
            },
        )
        append_assessment_event(
            event_type="attempt_closed",
            event_version=1,
            event_domain="assessment",
            actor_source="server",
            session=attempt.session,
            assignment=attempt.assignment,
            attempt=attempt,
            question_snapshot=attempt.assignment.snapshot,
            item_instance=attempt.assignment.item_instance,
            payload={"close_reason": "timed_out"},
        )

        self.client.force_login(self.teacher)
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/experiments/{self.experiment.pk}/questions/"
            f"{self.single_snapshot.pk}/students/{self.students['A'].pk}/process/"
        )
        payload = domain_payload(response)
        self.assertEqual(response.status_code, 200)
        serialized = str(payload)
        for expected in ("已前往下一题", "作答超时"):
            self.assertIn(expected, serialized)
        self.assertNotIn(f"目标分配 {destination.pk}", serialized)
        self.assertNotIn("timed_out", serialized)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_process_page_pending_coverage_uses_chinese_finalization_message(self):
        self.client.force_login(self.teacher)
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/experiments/{self.experiment.pk}/questions/"
            f"{self.single_snapshot.pk}/students/{self.students['C'].pk}/process/"
        )
        payload = domain_payload(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["detail"]["coverage_status_title"], "过程记录等待确认")
        serialized = str(payload)
        for forbidden in ("pending_finalization", "unknown", "CoverageRun"):
            self.assertNotIn(forbidden, serialized)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_process_page_without_coverage_outside_grace_uses_legacy_message(self):
        attempt = self.attempts[("B", "single")]
        ExperimentSession.objects.filter(pk=attempt.session_id).update(
            terminal_at=timezone.now() - timedelta(minutes=6),
        )

        self.client.force_login(self.teacher)
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/experiments/{self.experiment.pk}/questions/"
            f"{self.single_snapshot.pk}/students/{self.students['B'].pk}/process/"
        )
        detail = domain_payload(response)["detail"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(detail["coverage_status_title"], "历史记录无法评估")
        self.assertEqual(
            detail["coverage_status_description"],
            "旧记录没有当前采集契约，不能按新标准判断。",
        )
        self.assertIn("尚未生成记录质量评估", detail["coverage_reason_labels"])
        serialized = str(detail)
        for forbidden in ("legacy_unknown", "coverage_run_missing", "unknown", "CoverageRun"):
            self.assertNotIn(forbidden, serialized)

    def test_cross_experiment_question_and_nonparticipant_fail_closed(self):
        outsider = User.objects.create_user(
            username="DRILL-OUT",
            student_number="DRILL-OUT",
            role=User.Role.STUDENT,
        )

        with self.assertRaises(LookupError):
            build_attempt_process_detail(
                self.class_group,
                experiment_id=self.experiment.pk,
                question_id=self.single_snapshot.pk,
                student_id=outsider.pk,
            )
        with self.assertRaises(LookupError):
            build_question_detail(
                self.class_group,
                experiment_id=self.experiment.pk + 999,
                question_id=self.single_snapshot.pk,
            )

    def test_current_student_detail_has_two_experiment_trend_and_kc_macro_comparison(self):
        second = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="第二次诊断",
        )
        ExperimentQuestion.objects.create(
            experiment=second,
            question=self.single_source_question,
            display_order=1,
        )
        save_participant_rules(second, [self.class_group], [], [])
        second_version = publish_experiment(second, self.teacher)
        second_class_snapshot = second_version.class_snapshots.get(
            source_class_group=self.class_group
        )
        second_question = second_version.question_snapshots.get(
            source_question=self.single_source_question
        )
        for key, final_answer, is_correct in (
            ("A", "B", True),
            ("D", "A", False),
        ):
            session = ExperimentSession.objects.create(
                version=second_version,
                student=self.students[key],
                class_snapshot=second_class_snapshot,
                class_snapshot_name=second_class_snapshot.display_name,
                status=ExperimentSession.Status.COMPLETED,
                completed_at=timezone.now(),
            )
            self._submit_choice(
                session,
                second_question,
                sequence=1,
                states=[final_answer],
                final_answer=final_answer,
                is_correct=is_correct,
            )

        detail = build_student_detail(
            self.class_group,
            student_id=self.students["A"].pk,
        )

        self.assertEqual(detail.completed_experiments, 2)
        self.assertEqual(detail.eligible_experiments, 2)
        self.assertEqual(len(detail.experiments), 2)
        self.assertEqual(len(detail.trend_rows), 2)
        self.assertTrue(detail.show_trend_chart)
        kvl = next(row for row in detail.kc_rows if row.name == "KVL")
        self.assertEqual(kvl.comparison.student_value.ratio, Decimal("1"))
        self.assertEqual(kvl.comparison.class_average.sample_size, 3)
        timestamps = {
            detail.generated_at,
            detail.correct_rate.generated_at,
            *(row.generated_at for row in detail.experiments),
            *(row.generated_at for row in detail.trend_rows),
            *(row.generated_at for row in detail.kc_rows),
        }
        self.assertEqual(timestamps, {detail.generated_at})

        with self.assertRaises(LookupError):
            build_student_detail(
                self.class_group,
                student_id=self.students["C"].pk,
            )

    def test_historical_student_experiment_detail_is_read_only_fact_view(self):
        detail = build_student_experiment_detail(
            self.class_group,
            experiment_id=self.experiment.pk,
            student_id=self.students["C"].pk,
        )

        self.assertFalse(detail.is_current_member)
        self.assertEqual(detail.completed, True)
        self.assertEqual(detail.correct_rate.ratio, Decimal("0.5"))
        self.assertEqual(detail.rank, 3)
        self.assertEqual(detail.rank_sample_size, 4)
        self.assertEqual(len(detail.questions), 2)
        single = next(row for row in detail.questions if row.question_id == self.single_snapshot.pk)
        self.assertEqual(single.first_answer, "A")
        self.assertEqual(single.final_answer, "A")
        self.assertEqual(single.revision_count, 0)
        self.assertTrue(single.process_complete)

    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_teacher_drilldown_pages_use_the_independent_frontend_and_keep_scope_checks(self):
        self.client.force_login(self.teacher)
        current_url = reverse(
            "teacher-class-student-detail",
            args=[self.course.pk, self.class_group.pk, self.students["A"].pk],
        )
        historical_url = reverse(
            "teacher-class-student-experiment-detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.students["C"].pk,
                self.experiment.pk,
            ],
        )
        question_url = reverse(
            "teacher-class-question-detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.single_snapshot.pk,
            ],
        )
        process_url = reverse(
            "teacher-class-question-process",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.single_snapshot.pk,
                self.students["C"].pk,
            ],
        )

        with patch.dict("os.environ", {"FRONTEND_INTERNAL_REDIRECT": "/__frontend/index.html"}):
            responses = [
                self.client.get(current_url),
                self.client.get(historical_url),
                self.client.get(question_url),
                self.client.get(process_url),
            ]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn("data", response.json())
            self.assertFalse(response.templates)

        historical_class_detail = reverse(
            "teacher-class-student-detail",
            args=[self.course.pk, self.class_group.pk, self.students["C"].pk],
        )
        self.assertEqual(self.client.get(historical_class_detail).status_code, 404)


    @override_settings(CLASS_CENTER_ENABLED=True)
    def test_drilldown_permissions_and_cross_scope_combinations_fail_closed(self):
        outsider = User.objects.create_user(
            username="drilldown-outsider-teacher",
            role=User.Role.TEACHER,
        )
        url = reverse(
            "teacher-class-question-detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.single_snapshot.pk,
            ],
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.teacher)
        wrong_question = reverse(
            "teacher-class-question-detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.single_snapshot.pk + 999,
            ],
        )
        wrong_student = reverse(
            "teacher-class-question-process",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.single_snapshot.pk,
                outsider.pk,
            ],
        )
        self.assertEqual(self.client.get(wrong_question).status_code, 404)
        self.assertEqual(self.client.get(wrong_student).status_code, 404)


    @override_settings(CLASS_CENTER_ENABLED=False)
    def test_new_drilldown_routes_are_closed_when_class_center_flag_is_off(self):
        self.client.force_login(self.teacher)
        url = reverse(
            "teacher-class-student-detail",
            args=[self.course.pk, self.class_group.pk, self.students["A"].pk],
        )

        self.assertEqual(self.client.get(url).status_code, 404)
