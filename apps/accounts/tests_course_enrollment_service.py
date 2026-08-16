from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import ClassGroup, Course, CourseEnrollment, User
from .student_import import ParsedStudent


class CourseEnrollmentImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="课程 A",
            code="A",
            term="2026春",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="课程 B",
            code="B",
            term="2026春",
        )
        self.class_a = ClassGroup.objects.create(course=self.course_a, name="一班")
        self.class_b = ClassGroup.objects.create(course=self.course_b, name="二班")

    def test_existing_student_account_is_reused_without_changing_password(self):
        student = User.objects.create_user(
            username="S1",
            student_number="S1",
            display_name="学生甲",
            role=User.Role.STUDENT,
            password="original-password",
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=self.class_a,
            student=student,
        )
        parsed = [ParsedStudent("S1", "学生甲", self.class_b.pk, str(self.class_b))]

        from .enrollment_services import enroll_parsed_students

        created_accounts, enrollments = enroll_parsed_students(
            self.course_b,
            self.class_b,
            parsed,
        )

        self.assertEqual(created_accounts, 0)
        self.assertEqual(enrollments, 1)
        self.assertEqual(User.objects.filter(student_number="S1").count(), 1)
        student.refresh_from_db()
        self.assertTrue(student.check_password("original-password"))
        self.assertTrue(CourseEnrollment.objects.filter(course=self.course_b, student=student).exists())

    def test_identity_mismatch_or_duplicate_course_enrollment_rolls_back_batch(self):
        student = User.objects.create_user(
            username="S1",
            student_number="S1",
            display_name="学生甲",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        parsed = [
            ParsedStudent("NEW", "新学生", self.class_b.pk, str(self.class_b)),
            ParsedStudent("S1", "不同姓名", self.class_b.pk, str(self.class_b)),
        ]

        from .enrollment_services import enroll_parsed_students

        with self.assertRaises(ValidationError):
            enroll_parsed_students(self.course_b, self.class_b, parsed)

        self.assertFalse(User.objects.filter(student_number="NEW").exists())
