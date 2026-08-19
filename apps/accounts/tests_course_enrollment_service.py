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

    def test_manual_enrollment_creates_account_with_standard_password(self):
        from .enrollment_services import enroll_single_student

        student, created = enroll_single_student(
            self.course_b,
            self.class_b,
            "00123",
            "学生甲",
        )

        self.assertTrue(created)
        self.assertEqual(student.student_number, "00123")
        self.assertTrue(student.check_password("S00123"))
        self.assertTrue(
            CourseEnrollment.objects.filter(
                course=self.course_b,
                class_group=self.class_b,
                student=student,
            ).exists()
        )

    def test_manual_enrollment_reuses_matching_account_without_changing_password(self):
        from .enrollment_services import enroll_single_student

        student = User.objects.create_user(
            username="S2",
            student_number="S2",
            display_name="学生乙",
            role=User.Role.STUDENT,
            password="original-password",
        )

        reused, created = enroll_single_student(
            self.course_b,
            self.class_b,
            "S2",
            "学生乙",
        )

        self.assertEqual(reused, student)
        self.assertFalse(created)
        student.refresh_from_db()
        self.assertTrue(student.check_password("original-password"))

    def test_manual_enrollment_rejects_identity_mismatch_or_existing_course_enrollment(self):
        from .enrollment_services import enroll_single_student

        student = User.objects.create_user(
            username="S3",
            student_number="S3",
            display_name="学生丙",
            role=User.Role.STUDENT,
        )
        with self.assertRaisesMessage(ValidationError, "姓名不一致"):
            enroll_single_student(self.course_b, self.class_b, "S3", "另一个姓名")

        CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        with self.assertRaisesMessage(ValidationError, "已加入当前课程"):
            enroll_single_student(self.course_b, self.class_b, "S3", "学生丙")

    def test_copy_class_enrollments_skips_target_course_conflicts_and_keeps_passwords(self):
        from .enrollment_services import copy_class_enrollments

        first = User.objects.create_user(
            username="C1",
            student_number="C1",
            display_name="复制甲",
            role=User.Role.STUDENT,
            password="first-password",
        )
        second = User.objects.create_user(
            username="C2",
            student_number="C2",
            display_name="复制乙",
            role=User.Role.STUDENT,
            password="second-password",
        )
        CourseEnrollment.objects.create(course=self.course_a, class_group=self.class_a, student=first)
        CourseEnrollment.objects.create(course=self.course_a, class_group=self.class_a, student=second)
        CourseEnrollment.objects.create(course=self.course_b, class_group=self.class_b, student=second)

        created, skipped = copy_class_enrollments(
            target_course=self.course_b,
            target_class=self.class_b,
            source_class=self.class_a,
        )

        self.assertEqual((created, skipped), (1, 1))
        self.assertTrue(CourseEnrollment.objects.filter(course=self.course_b, student=first).exists())
        self.assertEqual(User.objects.filter(student_number__in=["C1", "C2"]).count(), 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.check_password("first-password"))
        self.assertTrue(second.check_password("second-password"))
