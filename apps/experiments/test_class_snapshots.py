from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.services import get_or_create_session
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import (
    allocate_course_question_identity,
)

from .models import (
    Experiment,
    ExperimentClassSnapshot,
    ExperimentQuestion,
    ExperimentVersionParticipantSnapshot,
)
from .services import publish_experiment, save_participant_rules


class ExperimentClassSnapshotTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="class-snapshot-teacher", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="class-snapshot-student",
            student_number="CLASS-S-001",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="班级快照课程",
            code="CLS101",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="电子2301班",
            academic_year="2023",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="班级快照题库",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="快照题",
            correct_answer="答案",
        )
        allocate_course_question_identity(self.course, question)
        concept = Concept.objects.create(course=self.course, name="班级快照 KC")
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="班级快照实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        save_participant_rules(self.experiment, [], [self.student], [])

    def test_publish_freezes_class_and_participant_snapshots(self):
        version = publish_experiment(self.experiment, self.teacher)

        class_snapshot = ExperimentClassSnapshot.objects.get(version=version)
        participant_snapshot = ExperimentVersionParticipantSnapshot.objects.get(
            version=version,
            student=self.student,
        )

        self.assertEqual(class_snapshot.source_class_group_id, self.class_group.pk)
        self.assertEqual(class_snapshot.name_snapshot, "电子2301班")
        self.assertEqual(class_snapshot.academic_year_snapshot, "2023")
        self.assertEqual(
            class_snapshot.capture_mode,
            ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        self.assertEqual(participant_snapshot.class_snapshot_id, class_snapshot.pk)

    def test_session_uses_immutable_class_snapshot_not_current_class_name(self):
        version = publish_experiment(self.experiment, self.teacher)
        class_snapshot = ExperimentClassSnapshot.objects.get(version=version)
        self.class_group.name = "已经改名的班级"
        self.class_group.academic_year = "2024"
        self.class_group.save(update_fields=["name", "academic_year"])

        session = get_or_create_session(self.experiment, self.student)

        self.assertEqual(session.version_id, version.pk)
        self.assertEqual(session.class_snapshot_id, class_snapshot.pk)
        self.assertEqual(session.class_snapshot_name, "2023 电子2301班")
        class_snapshot.refresh_from_db()
        self.assertEqual(class_snapshot.name_snapshot, "电子2301班")
        self.assertEqual(class_snapshot.academic_year_snapshot, "2023")

    def test_native_roster_snapshot_rejects_an_enrolled_unassigned_student(self):
        other_student = User.objects.create_user(
            username="class-snapshot-unassigned",
            student_number="CLASS-S-002",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=other_student,
        )
        publish_experiment(self.experiment, self.teacher)

        with self.assertRaises(ValidationError):
            get_or_create_session(self.experiment, other_student)
