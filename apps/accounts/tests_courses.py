from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import ClassGroup, User


def optional_model(name):
    try:
        return apps.get_model("accounts", name)
    except LookupError:
        return None


class CourseModelTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        self.collaborator = User.objects.create_user(username="collaborator", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="S100",
            student_number="S100",
            role=User.Role.STUDENT,
        )

    def test_course_models_exist_with_required_identity_and_memberships(self):
        Course = optional_model("Course")
        CourseTeacher = optional_model("CourseTeacher")
        CourseEnrollment = optional_model("CourseEnrollment")
        self.assertIsNotNone(Course)
        self.assertIsNotNone(CourseTeacher)
        self.assertIsNotNone(CourseEnrollment)

        course = Course.objects.create(
            name="高等数学",
            code="MATH101",
            term="2026春",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=course, teacher=self.collaborator)
        self.assertTrue(course.can_manage(self.owner))
        self.assertTrue(course.can_manage(self.collaborator))
        self.assertFalse(course.can_manage(self.student))

        duplicate = Course(name="另一个名称", code="MATH101", term="2026春", owner=self.owner)
        with self.assertRaises(ValidationError):
            duplicate.validate_constraints()

    def test_enrollment_supports_different_classes_per_course_and_rejects_cross_course_class(self):
        Course = optional_model("Course")
        CourseEnrollment = optional_model("CourseEnrollment")
        self.assertIsNotNone(Course)
        self.assertIsNotNone(CourseEnrollment)

        course_a = Course.objects.create(name="课程 A", code="A", term="2026春", owner=self.owner)
        course_b = Course.objects.create(name="课程 B", code="B", term="2026春", owner=self.owner)
        class_a = ClassGroup.objects.create(course=course_a, name="一班", academic_year="2026")
        class_b = ClassGroup.objects.create(course=course_b, name="二班", academic_year="2026")

        enrollment_a = CourseEnrollment(course=course_a, student=self.student, class_group=class_a)
        enrollment_a.full_clean()
        enrollment_a.save()
        enrollment_b = CourseEnrollment(course=course_b, student=self.student, class_group=class_b)
        enrollment_b.full_clean()
        enrollment_b.save()
        self.assertEqual(self.student.course_enrollments.count(), 2)

        invalid = CourseEnrollment(course=course_a, student=self.student, class_group=class_b)
        with self.assertRaises(ValidationError):
            invalid.full_clean()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CourseEnrollment.objects.create(
                    course=course_a,
                    student=self.student,
                    class_group=class_a,
                )
