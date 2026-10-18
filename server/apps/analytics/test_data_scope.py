from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, User
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import (
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .data_scope import DataScope, ScopeNotFound, ScopeValidationError
from .teaching_data import TeachingScopeBlocked, require_single_version_scope


class DataScopeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="scope-teacher", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="scope-student", role=User.Role.STUDENT, student_number="SCOPE-1"
        )
        self.other_student = User.objects.create_user(
            username="scope-other-student",
            role=User.Role.STUDENT,
            student_number="SCOPE-2",
        )
        self.course = Course.objects.create(
            owner=self.teacher, name="范围课程", code="SCOPE101", term="2026 秋"
        )
        self.other_course = Course.objects.create(
            owner=self.teacher, name="其他范围课程", code="SCOPE102", term="2026 秋"
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course, name="电子2301班", academic_year="2023"
        )
        self.bank = QuestionBank.objects.create(
            course=self.course, created_by=self.teacher, name="范围题库"
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="范围实验",
        )
        self.other_experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="同课程另一实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment, version_number=1, published_by=self.teacher
        )
        self.other_version = ExperimentVersion.objects.create(
            experiment=self.other_experiment, version_number=1, published_by=self.teacher
        )
        self.class_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=self.class_group,
            name_snapshot="电子2301班",
            academic_year_snapshot="2023",
            capture_mode=ExperimentClassSnapshot.CaptureMode.LEGACY_LABEL_PROJECTION,
        )
        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="范围题",
            correct_answer="答案",
        )
        self.question_snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=question,
            source_sequence=1,
            code="SCOPE-Q1",
            question_type=Question.Type.SHORT,
            text="范围题",
            correct_answer="答案",
            primary_kc="范围 KC",
            all_kcs=["范围 KC"],
        )
        self.session = ExperimentSession.objects.create(
            version=self.version,
            student=self.student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
        )
        self.assignment = StudentQuestionAssignment.objects.create(
            session=self.session,
            snapshot=self.question_snapshot,
            item_instance=get_or_create_default_item_instance(self.question_snapshot),
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(session=self.session, assignment=self.assignment)

    def reference(self, session=None):
        session = session or self.session
        return f"S{session.started_at.astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}_{session.pk:04d}"

    def test_session_reference_does_not_enumerate_course_history(self):
        with patch.object(DataScope, "session_references", side_effect=AssertionError("full-course scan")):
            scope = DataScope.from_mapping(self.course, {"session_ref": self.reference()})
            self.assertEqual(list(scope.sessions().values_list("pk", flat=True)), [self.session.pk])

    def test_search_formats_only_sessions_in_the_filtered_queryset(self):
        scope = DataScope.from_mapping(self.course, {
            "experiment_id": self.experiment.pk, "search": self.reference().lower(),
        })
        with patch.object(DataScope, "session_references", side_effect=AssertionError("full-course scan")):
            self.assertEqual(list(scope.sessions().values_list("pk", flat=True)), [self.session.pk])

    def test_session_reference_requires_canonical_date_and_course(self):
        for reference in (f"S19000101_{self.session.pk:04d}", f"S20261018_{self.session.pk}", "not-a-reference"):
            with self.subTest(reference=reference), self.assertRaises(ScopeNotFound):
                DataScope.from_mapping(self.course, {"session_ref": reference})
        with self.assertRaises(ScopeNotFound):
            DataScope.from_mapping(self.other_course, {"session_ref": self.reference()})

    def test_session_reference_rejects_overlong_primary_key(self):
        for digits in (20, 4300, 4301):
            with self.subTest(digits=digits), self.assertNumQueries(0):
                with self.assertRaises(ScopeNotFound):
                    DataScope.from_mapping(self.course, {
                        "session_ref": "S20261018_" + "9" * digits,
                    })

    def test_session_reference_rejects_conflicting_session_or_experiment(self):
        other = ExperimentSession.objects.create(version=self.other_version, student=self.other_student)
        for values in ({"session_id": other.pk}, {"experiment_id": self.other_experiment.pk}):
            with self.subTest(values=values), self.assertRaises(ScopeValidationError):
                DataScope.from_mapping(self.course, {"session_ref": self.reference(), **values})

    def test_experiment_list_validation_uses_one_query_and_rejects_missing_ids(self):
        with CaptureQueriesContext(connection) as queries:
            DataScope.from_mapping(self.course, {"experiment_ids": f"{self.experiment.pk},{self.other_experiment.pk}"})
        self.assertEqual(len(queries), 1)
        with self.assertRaises(ScopeNotFound):
            DataScope.from_mapping(self.course, {"experiment_ids": f"{self.experiment.pk},99999999"})

    def test_live_class_scope_intersects_all_matching_published_snapshots(self):
        version_two = ExperimentVersion.objects.create(
            experiment=self.experiment, version_number=2, published_by=self.teacher
        )
        snapshot_two = ExperimentClassSnapshot.objects.create(
            version=version_two,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        second_session = ExperimentSession.objects.create(
            version=version_two,
            student=self.student,
            class_snapshot=snapshot_two,
            class_snapshot_name=snapshot_two.display_name,
        )

        scope = DataScope.from_mapping(
            self.course,
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
        )

        self.assertEqual(
            set(scope.sessions().values_list("pk", flat=True)),
            {self.session.pk, second_session.pk},
        )
        self.assertEqual(scope.serialize()["class_id"], self.class_group.pk)

    def test_live_class_without_experiment_snapshot_is_empty_not_experiment_wide(self):
        unrelated_class = ClassGroup.objects.create(
            course=self.course, name="未参与实验班", academic_year="2023"
        )
        scope = DataScope.from_mapping(
            self.course,
            {"experiment_id": self.experiment.pk, "class_id": unrelated_class.pk},
        )

        self.assertFalse(scope.sessions().exists())
        self.assertFalse(scope.attempts().exists())

    def test_scope_serializes_immutable_class_snapshot_and_intersects_filters(self):
        scope = DataScope.from_mapping(
            self.course,
            {
                "experiment_id": self.experiment.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "class_snapshot_id": str(self.class_snapshot.pk),
                "student_id": self.student.pk,
                "session_id": self.session.pk,
            },
        )

        self.assertEqual(list(scope.sessions().values_list("pk", flat=True)), [self.session.pk])
        self.assertEqual(list(scope.attempts().values_list("pk", flat=True)), [self.attempt.pk])
        self.assertEqual(
            scope.serialize(),
            {
                "scope_version": 1,
                "experiment_id": self.experiment.pk,
                "version_mode": "single",
                "version_id": self.version.pk,
                "class_snapshot_id": str(self.class_snapshot.pk),
                "student_id": self.student.pk,
                "session_id": self.session.pk,
            },
        )

    def test_same_course_contradictory_filters_are_rejected(self):
        with self.assertRaises(ScopeValidationError):
            DataScope.from_mapping(
                self.course,
                {
                    "experiment_id": self.experiment.pk,
                    "version_mode": "single",
                    "version_id": self.other_version.pk,
                },
            )

    def test_foreign_session_is_not_exposed(self):
        other_bank = QuestionBank.objects.create(
            course=self.other_course, created_by=self.teacher, name="其他题库"
        )
        other_experiment = Experiment.objects.create(
            course=self.other_course,
            created_by=self.teacher,
            question_bank=other_bank,
            name="其他实验",
        )
        other_version = ExperimentVersion.objects.create(
            experiment=other_experiment, version_number=1, published_by=self.teacher
        )
        foreign_session = ExperimentSession.objects.create(
            version=other_version,
            student=self.other_student,
        )

        with self.assertRaises(ScopeNotFound):
            DataScope.from_mapping(self.course, {"session_id": foreign_session.pk})

    def test_time_window_filters_sessions_not_attempt_timestamps(self):
        started_at = timezone.now() - timedelta(hours=2)
        ExperimentSession.objects.filter(pk=self.session.pk).update(started_at=started_at)
        Attempt.objects.filter(pk=self.attempt.pk).update(submitted_at=timezone.now())

        scope = DataScope.from_mapping(
            self.course,
            {
                "started_from_utc": (started_at - timedelta(minutes=1)).isoformat(),
                "started_before_utc": (started_at + timedelta(minutes=1)).isoformat(),
            },
        )

        self.assertEqual(list(scope.attempts().values_list("pk", flat=True)), [self.attempt.pk])

    def test_teaching_requires_an_explicit_single_version(self):
        all_versions = DataScope.from_mapping(
            self.course,
            {"experiment_id": self.experiment.pk, "version_mode": "all"},
        )

        with self.assertRaises(TeachingScopeBlocked):
            require_single_version_scope(all_versions)
