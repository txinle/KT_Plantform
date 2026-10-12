from django.apps import apps as django_apps
from django.test import TestCase

from apps.experiments.models import Experiment, ExperimentStudent, ExperimentVersion
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import ClassGroup, Course, CourseEnrollment, User


class LegacyCourseBackfillTests(TestCase):
    def test_backfill_preserves_objects_and_builds_course_scoped_relations(self):
        teacher = User.objects.create_user(username="legacy-teacher", role=User.Role.TEACHER)
        student = User.objects.create_user(
            username="LEGACY-S1",
            student_number="LEGACY-S1",
            role=User.Role.STUDENT,
        )
        class_group = ClassGroup.objects.create(
            owner=teacher,
            name="旧班级",
            academic_year="2025",
        )
        student.class_group = class_group
        student.save(update_fields=["class_group"])
        bank = QuestionBank.objects.create(owner=teacher, name="旧题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="旧题目",
            correct_answer="答案",
        )
        concept = Concept.objects.create(
            owner=teacher,
            scope=Concept.Scope.PRIVATE,
            name="旧 KC",
        )
        link = QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        experiment = Experiment.objects.create(owner=teacher, question_bank=bank, name="旧实验")
        ExperimentStudent.objects.create(experiment=experiment, student=student)
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=teacher,
        )
        original_ids = (class_group.pk, bank.pk, question.pk, experiment.pk, version.pk)

        from .course_migration import backfill_legacy_courses

        backfill_legacy_courses(django_apps, None)

        course = Course.objects.get(owner=teacher)
        class_group.refresh_from_db()
        bank.refresh_from_db()
        experiment.refresh_from_db()
        version.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(class_group.course, course)
        self.assertEqual(bank.course, course)
        self.assertEqual(experiment.course, course)
        self.assertEqual(link.concept.course, course)
        enrollment = CourseEnrollment.objects.get(course=course, student=student)
        self.assertEqual(enrollment.class_group, class_group)
        self.assertEqual(version.course_name_snapshot, course.name)
        self.assertEqual(version.course_code_snapshot, course.code)
        self.assertEqual(version.course_term_snapshot, course.term)
        self.assertEqual(
            (class_group.pk, bank.pk, question.pk, experiment.pk, version.pk),
            original_ids,
        )

    def test_backfill_includes_admin_owned_legacy_resources(self):
        admin = User.objects.create_user(username="legacy-admin", role=User.Role.ADMIN)
        class_group = ClassGroup.objects.create(owner=admin, name="管理员旧班级")
        bank = QuestionBank.objects.create(owner=admin, name="管理员旧题库")
        experiment = Experiment.objects.create(owner=admin, question_bank=bank, name="管理员旧实验")

        from .course_migration import backfill_legacy_courses

        backfill_legacy_courses(django_apps, None)

        course = Course.objects.get(owner=admin)
        class_group.refresh_from_db()
        bank.refresh_from_db()
        experiment.refresh_from_db()
        self.assertEqual(class_group.course, course)
        self.assertEqual(bank.course, course)
        self.assertEqual(experiment.course, course)

    def test_backfill_deduplicates_same_normalized_concepts_on_one_question(self):
        teacher = User.objects.create_user(username="collision-teacher", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(owner=teacher, name="冲突旧题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="冲突题目",
            correct_answer="答案",
        )
        public = Concept.objects.create(scope=Concept.Scope.PUBLIC, name="函数")
        private = Concept.objects.create(owner=teacher, scope=Concept.Scope.PRIVATE, name="  函数  ")
        QuestionConcept.objects.create(question=question, concept=public, is_primary=False)
        QuestionConcept.objects.create(question=question, concept=private, is_primary=True)

        from .course_migration import backfill_legacy_courses

        backfill_legacy_courses(django_apps, None)

        links = list(question.concept_links.select_related("concept"))
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].is_primary)
        self.assertEqual(links[0].concept.course.owner, teacher)
