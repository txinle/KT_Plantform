from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.assessment.models import ExperimentSession
from apps.experiments.models import Experiment, ExperimentStudent, ExperimentVersion
from apps.question_bank.models import QuestionBank

from .models import ClassGroup, Course, CourseEnrollment, User


def research_model(name):
    try:
        return django_apps.get_model("accounts", name)
    except LookupError:
        return None


class ResearchSubjectModelTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="subject-owner",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="SUBJECT-S001",
            student_number="SUBJECT-S001",
            role=User.Role.STUDENT,
        )
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="课程 A",
            code="SUB-A",
            term="2026 秋",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="课程 B",
            code="SUB-B",
            term="2027 春",
        )

    def test_research_identity_models_exist(self):
        for name in (
            "CourseSubject",
            "ResearchStudy",
            "ResearchStudyCourse",
            "ResearchStudyParticipant",
        ):
            self.assertIsNotNone(research_model(name), name)

    def test_course_subject_is_stable_within_course_and_distinct_across_courses(self):
        CourseSubject = research_model("CourseSubject")
        self.assertIsNotNone(CourseSubject)

        first = CourseSubject.objects.get_or_create(
            course=self.course_a,
            student=self.student,
        )[0]
        repeated = CourseSubject.objects.get_or_create(
            course=self.course_a,
            student=self.student,
        )[0]
        other_course = CourseSubject.objects.get_or_create(
            course=self.course_b,
            student=self.student,
        )[0]

        self.assertEqual(first.public_uuid, repeated.public_uuid)
        self.assertNotEqual(first.public_uuid, other_course.public_uuid)
        self.assertNotEqual(str(first.public_uuid), str(self.student.pk))

    def test_course_subject_pair_has_a_database_uniqueness_constraint(self):
        CourseSubject = research_model("CourseSubject")
        self.assertIsNotNone(CourseSubject)
        CourseSubject.objects.create(course=self.course_a, student=self.student)

        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseSubject.objects.create(course=self.course_a, student=self.student)

    def test_study_subject_is_stable_only_inside_the_explicit_study(self):
        ResearchStudy = research_model("ResearchStudy")
        ResearchStudyParticipant = research_model("ResearchStudyParticipant")
        self.assertIsNotNone(ResearchStudy)
        self.assertIsNotNone(ResearchStudyParticipant)
        first_study = ResearchStudy.objects.create(name="纵向研究 A", owner=self.teacher)
        second_study = ResearchStudy.objects.create(name="纵向研究 B", owner=self.teacher)

        first = ResearchStudyParticipant.objects.get_or_create(
            study=first_study,
            student=self.student,
        )[0]
        repeated = ResearchStudyParticipant.objects.get_or_create(
            study=first_study,
            student=self.student,
        )[0]
        other_study = ResearchStudyParticipant.objects.get_or_create(
            study=second_study,
            student=self.student,
        )[0]

        self.assertEqual(first.public_uuid, repeated.public_uuid)
        self.assertNotEqual(first.public_uuid, other_study.public_uuid)

    def test_study_course_pair_has_a_database_uniqueness_constraint(self):
        ResearchStudy = research_model("ResearchStudy")
        ResearchStudyCourse = research_model("ResearchStudyCourse")
        self.assertIsNotNone(ResearchStudy)
        self.assertIsNotNone(ResearchStudyCourse)
        study = ResearchStudy.objects.create(name="课程关联研究", owner=self.teacher)
        ResearchStudyCourse.objects.create(study=study, course=self.course_a)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ResearchStudyCourse.objects.create(study=study, course=self.course_a)

    def test_existing_enrollment_backfill_is_idempotent(self):
        CourseSubject = research_model("CourseSubject")
        self.assertIsNotNone(CourseSubject)
        class_group = ClassGroup.objects.create(
            course=self.course_a,
            owner=self.teacher,
            name="1 班",
            academic_year="2026",
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            student=self.student,
            class_group=class_group,
        )

        from .research_subjects import backfill_course_subjects

        backfill_course_subjects(django_apps, None)
        original_uuid = CourseSubject.objects.get(
            course=self.course_a,
            student=self.student,
        ).public_uuid
        backfill_course_subjects(django_apps, None)

        self.assertEqual(CourseSubject.objects.count(), 1)
        self.assertEqual(
            CourseSubject.objects.get(
                course=self.course_a,
                student=self.student,
            ).public_uuid,
            original_uuid,
        )

    def test_backfill_includes_course_experiment_participants_and_sessions(self):
        CourseSubject = research_model("CourseSubject")
        self.assertIsNotNone(CourseSubject)
        participant_only = User.objects.create_user(
            username="SUBJECT-S002",
            student_number="SUBJECT-S002",
            role=User.Role.STUDENT,
        )
        session_only = User.objects.create_user(
            username="SUBJECT-S003",
            student_number="SUBJECT-S003",
            role=User.Role.STUDENT,
        )
        bank_a = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            owner=self.teacher,
            name="课程 A 题库",
        )
        bank_b = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            owner=self.teacher,
            name="课程 B 题库",
        )
        experiment_a = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            owner=self.teacher,
            question_bank=bank_a,
            name="课程 A 实验",
        )
        experiment_b = Experiment.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            owner=self.teacher,
            question_bank=bank_b,
            name="课程 B 实验",
        )
        ExperimentStudent.objects.create(
            experiment=experiment_a,
            student=participant_only,
        )
        version_b = ExperimentVersion.objects.create(
            experiment=experiment_b,
            version_number=1,
            published_by=self.teacher,
        )
        ExperimentSession.objects.create(version=version_b, student=session_only)

        from .research_subjects import backfill_course_subjects

        backfill_course_subjects(django_apps, None)

        self.assertTrue(
            CourseSubject.objects.filter(
                course=self.course_a,
                student=participant_only,
            ).exists()
        )
        self.assertTrue(
            CourseSubject.objects.filter(
                course=self.course_b,
                student=session_only,
            ).exists()
        )
