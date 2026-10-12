from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.models import Attempt, ExperimentSession
from apps.experiments.models import (
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
)
from apps.question_bank.models import QuestionBank

from .teaching_data import TimingKind, build_teaching_roster, summarize_attempt_timing
from .teaching_scope import TeachingScope


class TimingSummaryTests(TestCase):
    def test_wall_clock_is_reported_when_activity_is_not_observable(self):
        attempt = SimpleNamespace(
            pk=None,
            active_seconds=9999,
            first_opened_at=timezone.now() - timedelta(minutes=8),
        )
        submission = SimpleNamespace(committed_at=timezone.now())

        summary = summarize_attempt_timing(attempt, events=[], submission=submission)

        self.assertEqual(summary.kind, TimingKind.WALL_CLOCK)
        self.assertGreaterEqual(summary.duration_ms, 8 * 60 * 1000 - 100)
        self.assertNotEqual(summary.duration_ms, attempt.active_seconds * 1000)
        self.assertEqual(summary.wall_clock_duration_ms, summary.display_duration_ms)


class TeachingRosterTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teaching-roster-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="教学 roster 课程",
            code="ROSTER101",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="电子 2301 班",
            academic_year="2026",
        )
        self.participant = User.objects.create_user(
            username="teaching-participant",
            student_number="ROSTER-001",
            role=User.Role.STUDENT,
        )
        self.not_participated = User.objects.create_user(
            username="teaching-not-participated",
            student_number="ROSTER-002",
            role=User.Role.STUDENT,
        )
        for student in (self.participant, self.not_participated):
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="教学 roster 题库",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="教学 roster 实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        class_snapshot = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        ExperimentSession.objects.create(
            version=version,
            student=self.participant,
            class_snapshot=class_snapshot,
            class_snapshot_name=class_snapshot.display_name,
        )

    def test_roster_includes_student_without_session_as_not_participated(self):
        scope = TeachingScope.from_mapping(
            self.course,
            {
                "experiment_id": self.experiment.pk,
                "class_id": self.class_group.pk,
            },
        )

        rows = build_teaching_roster(scope)

        self.assertEqual(
            [row["student"].student_number for row in rows],
            ["ROSTER-001", "ROSTER-002"],
        )
        absent = rows[1]
        self.assertEqual(absent["data_status"], "not_participated")
        self.assertIsNone(absent["attempt_count"])
        self.assertIsNone(absent["graded_count"])
        self.assertIsNone(absent["score"])
