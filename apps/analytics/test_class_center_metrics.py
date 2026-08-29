import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
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
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .teaching.contracts import DataState
from .teaching.comparisons import competition_ranks
from .teaching.metrics import build_class_center_snapshot


class ClassCenterMetricContractTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="class-center-metric-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="班级中心统计课程",
            code="CLASS-CENTER-METRICS",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="统计实验班",
            academic_year="2026",
        )
        self.student_a = self._enroll_student("METRIC-A", "学生 A")
        self.student_b = self._enroll_student("METRIC-B", "学生 B")
        self.student_c = self._enroll_student("METRIC-C", "学生 C")
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="班级中心统计题库",
        )
        concept_a = Concept.objects.create(course=self.course, name="KC A")
        concept_b = Concept.objects.create(course=self.course, name="KC B")
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="双口径诊断实验",
        )
        for sequence in range(1, 11):
            question = Question.objects.create(
                bank=bank,
                sequence=sequence,
                question_type=Question.Type.SHORT,
                text=f"统计题 {sequence}",
                correct_answer="正确",
            )
            QuestionConcept.objects.create(
                question=question,
                concept=concept_a if sequence == 1 else concept_b,
                is_primary=True,
            )
            ExperimentQuestion.objects.create(
                experiment=self.experiment,
                question=question,
                display_order=sequence,
            )
        save_participant_rules(self.experiment, [self.class_group], [], [])
        self.version = publish_experiment(self.experiment, self.teacher)
        self.class_snapshot = self.version.class_snapshots.get(
            source_class_group=self.class_group
        )
        snapshots = list(self.version.question_snapshots.order_by("source_sequence"))
        self._grade_student(self.student_a, snapshots[:1], correct_count=1)
        self._grade_student(self.student_b, snapshots, correct_count=5)

    def _enroll_student(self, student_number, display_name):
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

    def _grade_student(self, student, snapshots, *, correct_count):
        session = ExperimentSession.objects.create(
            version=self.version,
            student=student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        for index, snapshot in enumerate(snapshots, start=1):
            assignment = StudentQuestionAssignment.objects.create(
                session=session,
                snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot),
                assigned_sequence_index=index,
            )
            is_correct = index <= correct_count
            attempt = Attempt.objects.create(
                session=session,
                assignment=assignment,
                status=Attempt.Status.SUBMITTED,
                submitted_at=timezone.now(),
                first_to_submit_duration_ms=index * 1000,
            )
            submission = SubmissionEvent.objects.create(
                attempt=attempt,
                submission_token=uuid.uuid4(),
                final_answer="正确" if is_correct else "错误",
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

    def _submit_without_effective_grade(self, student, snapshot):
        session = ExperimentSession.objects.create(
            version=self.version,
            student=student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            submitted_at=timezone.now(),
            first_to_submit_duration_ms=3000,
        )
        return SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="等待评分",
            grading_status=SubmissionEvent.GradingStatus.PENDING,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )

    def test_all_experiments_uses_roster_micro_and_macro_contracts(self):
        snapshot = build_class_center_snapshot(self.class_group)

        self.assertEqual(snapshot.overview.roster_count, 3)
        self.assertEqual(snapshot.overview.participated_count, 2)
        self.assertEqual(snapshot.overview.correct_rate.numerator, Decimal("6"))
        self.assertEqual(snapshot.overview.correct_rate.denominator, Decimal("11"))
        self.assertEqual(
            snapshot.overview.correct_rate.ratio,
            Decimal("6") / Decimal("11"),
        )
        self.assertEqual(snapshot.class_average_correct_rate.ratio, Decimal("0.75"))
        self.assertEqual(snapshot.class_average_correct_rate.sample_size, 2)
        self.assertEqual(snapshot.overview.completion_rate.ratio, Decimal("2") / Decimal("3"))

        rows = {row.student_number: row for row in snapshot.students.items}
        self.assertEqual(set(rows), {"METRIC-A", "METRIC-B", "METRIC-C"})
        self.assertEqual(rows["METRIC-A"].correct_rate.ratio, Decimal("1"))
        self.assertEqual(rows["METRIC-B"].correct_rate.ratio, Decimal("0.5"))
        self.assertEqual(rows["METRIC-C"].correct_rate.state, DataState.NO_DATA)
        self.assertEqual(rows["METRIC-C"].status, "not_participated")
        self.assertIsNone(rows["METRIC-A"].rank)

        distribution = {row.key: row.count for row in snapshot.correct_rate_distribution}
        self.assertEqual(distribution["lt_60"], 1)
        self.assertEqual(distribution["90_100"], 1)
        self.assertEqual(distribution["no_data"], 1)

    def test_single_experiment_scope_enables_only_complete_score_ranks(self):
        snapshot = build_class_center_snapshot(
            self.class_group,
            experiment_id=self.experiment.pk,
            sort="rank",
        )

        rows = {row.student_number: row for row in snapshot.students.items}
        self.assertEqual(rows["METRIC-A"].rank, 1)
        self.assertEqual(rows["METRIC-B"].rank, 2)
        self.assertIsNone(rows["METRIC-C"].rank)
        self.assertEqual([row.student_number for row in snapshot.students.items[:2]], ["METRIC-A", "METRIC-B"])
        self.assertEqual(snapshot.scope.experiment_id, self.experiment.pk)
        self.assertEqual(snapshot.rank_sample_size, 2)
        self.assertTrue(snapshot.has_live_experiments)
        self.assertEqual(snapshot.question_rows[0].answer_count, 2)
        self.assertEqual(snapshot.question_rows[0].correct_count, 2)

    def test_metric_sort_keeps_no_data_after_comparable_students(self):
        ascending = build_class_center_snapshot(
            self.class_group,
            sort="correct_rate",
        )
        descending = build_class_center_snapshot(
            self.class_group,
            sort="-correct_rate",
        )

        self.assertEqual(
            [row.student_number for row in ascending.students.items],
            ["METRIC-B", "METRIC-A", "METRIC-C"],
        )
        self.assertEqual(
            [row.student_number for row in descending.students.items],
            ["METRIC-A", "METRIC-B", "METRIC-C"],
        )

    def test_student_search_status_filter_and_page_are_server_side(self):
        snapshot = build_class_center_snapshot(
            self.class_group,
            query="METRIC-C",
            status="not_participated",
            page=1,
            page_size=1,
        )

        self.assertEqual(snapshot.students.total_count, 1)
        self.assertEqual(len(snapshot.students.items), 1)
        self.assertEqual(snapshot.students.items[0].student_number, "METRIC-C")
        self.assertEqual(snapshot.students.page, 1)
        self.assertEqual(snapshot.students.page_count, 1)

    def test_pending_submission_counts_as_answer_but_not_as_graded_result(self):
        first_question = self.version.question_snapshots.order_by("source_sequence").first()
        self._submit_without_effective_grade(self.student_c, first_question)

        snapshot = build_class_center_snapshot(
            self.class_group,
            experiment_id=self.experiment.pk,
        )

        first_row = snapshot.question_rows[0]
        self.assertEqual(first_row.answer_count, 3)
        self.assertEqual(first_row.average_answer_time_display, "1.7s")
        self.assertEqual(first_row.correct_rate.numerator, Decimal("2"))
        self.assertEqual(first_row.correct_rate.denominator, Decimal("2"))
        students = {row.student_number: row for row in snapshot.students.items}
        self.assertEqual(students["METRIC-C"].correct_rate.state, DataState.NO_DATA)
        self.assertEqual(students["METRIC-C"].score_rate.state, DataState.NO_DATA)
        self.assertEqual(students["METRIC-C"].average_answer_time_display, "3.0s")
        self.assertIsNone(students["METRIC-C"].rank)

    def test_completed_regrade_replaces_instead_of_double_counting(self):
        original = GradeRecord.objects.filter(
            submission__attempt__session__student=self.student_b,
            is_correct=True,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).first()
        original.validity_status = GradeRecord.ValidityStatus.SUPERSEDED
        original.save(update_fields=["validity_status"])
        GradeRecord.objects.create(
            submission=original.submission,
            version=2,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=False,
            score=Decimal("0"),
            max_score=Decimal("1"),
            supersedes_grade=original,
        )

        snapshot = build_class_center_snapshot(self.class_group)

        self.assertEqual(snapshot.overview.correct_rate.numerator, Decimal("5"))
        self.assertEqual(snapshot.overview.correct_rate.denominator, Decimal("11"))

    def test_invalidated_session_and_archived_task_are_excluded(self):
        ExperimentSession.objects.filter(
            version=self.version,
            student=self.student_a,
        ).update(status=ExperimentSession.Status.INVALIDATED)

        snapshot = build_class_center_snapshot(self.class_group)
        students = {row.student_number: row for row in snapshot.students.items}

        self.assertEqual(snapshot.overview.participated_count, 1)
        self.assertEqual(snapshot.overview.correct_rate.numerator, Decimal("5"))
        self.assertEqual(snapshot.overview.correct_rate.denominator, Decimal("10"))
        self.assertEqual(students["METRIC-A"].status, "not_participated")
        self.assertEqual(students["METRIC-A"].correct_rate.state, DataState.NO_DATA)

        Experiment.objects.filter(pk=self.experiment.pk).update(
            status=Experiment.Status.ARCHIVED
        )
        archived = build_class_center_snapshot(self.class_group)
        self.assertEqual(archived.scope.task_count, 0)
        self.assertEqual(archived.overview.participated_count, 0)
        self.assertEqual(archived.overview.correct_rate.state, DataState.NO_DATA)

    def test_selected_experiment_keeps_historical_frozen_participant_outside_current_roster_metrics(self):
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_b,
        ).update(is_active=False)

        snapshot = build_class_center_snapshot(
            self.class_group,
            experiment_id=self.experiment.pk,
        )

        self.assertEqual(snapshot.overview.roster_count, 2)
        self.assertEqual(snapshot.overview.participated_count, 1)
        self.assertEqual(snapshot.students.total_count, 2)
        self.assertEqual(snapshot.experiment_rows[0].eligible_count, 3)
        self.assertEqual(snapshot.experiment_rows[0].participated_count, 2)
        self.assertEqual(snapshot.question_rows[0].answer_count, 2)
        participants = {
            row.student_number: row for row in snapshot.experiment_participants
        }
        self.assertEqual(set(participants), {"METRIC-A", "METRIC-B", "METRIC-C"})
        self.assertFalse(participants["METRIC-B"].is_current_member)

    def test_dtos_are_frozen_and_share_one_generated_at(self):
        snapshot = build_class_center_snapshot(self.class_group)

        with self.assertRaises(FrozenInstanceError):
            snapshot.overview.roster_count = 99
        timestamps = {
            snapshot.generated_at,
            snapshot.scope.generated_at,
            snapshot.overview.generated_at,
            snapshot.overview.correct_rate.generated_at,
            snapshot.students.generated_at,
            *(row.generated_at for row in snapshot.students.items),
            *(row.generated_at for row in snapshot.kc_rows),
            *(row.generated_at for row in snapshot.experiment_rows),
        }
        self.assertEqual(timestamps, {snapshot.generated_at})

    def test_zero_is_available_data_and_not_missing_data(self):
        first_question = self.version.question_snapshots.order_by("source_sequence").first()
        submission = self._submit_without_effective_grade(self.student_c, first_question)
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=False,
            score=Decimal("0"),
            max_score=Decimal("1"),
        )

        snapshot = build_class_center_snapshot(
            self.class_group,
            experiment_id=self.experiment.pk,
        )
        student_c = next(
            row for row in snapshot.students.items if row.student_number == "METRIC-C"
        )

        self.assertEqual(student_c.correct_rate.state, DataState.AVAILABLE)
        self.assertEqual(student_c.correct_rate.ratio, Decimal("0"))
        self.assertEqual(student_c.score_rate.state, DataState.AVAILABLE)
        self.assertEqual(student_c.score_rate.ratio, Decimal("0"))

    def test_competition_ranking_uses_unrounded_values(self):
        ranks = competition_ranks(
            (
                (1, Decimal("0.9001"), "A"),
                (2, Decimal("0.50001"), "B"),
                (3, Decimal("0.50001"), "C"),
                (4, Decimal("0.49999"), "D"),
                (5, None, "E"),
            )
        )

        self.assertEqual(ranks, {1: 1, 2: 2, 3: 2, 4: 4})

    def test_query_budget_is_constant_for_the_fixture(self):
        with CaptureQueriesContext(connection) as captured:
            build_class_center_snapshot(self.class_group)

        budget = 10 if connection.vendor == "postgresql" else 9
        self.assertLessEqual(len(captured), budget)
