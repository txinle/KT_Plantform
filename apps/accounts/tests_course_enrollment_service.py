from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .models import (
    ClassGroup,
    Course,
    CourseEnrollment,
    CourseEnrollmentAccessPeriod,
    User,
)
from . import enrollment_services
from .student_import import ParsedStudent, StudentImportError


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

    def test_remove_preserves_enrollment_and_closes_access_period(self):
        student = User.objects.create_user(
            username="REMOVE-1",
            student_number="REMOVE-1",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        from .enrollment_access import has_course_access, open_enrollment_access

        period = open_enrollment_access(enrollment)
        enrollment_services.remove_course_enrollment(
            course=self.course_b,
            class_group=self.class_b,
            enrollment=enrollment,
        )

        enrollment.refresh_from_db()
        period.refresh_from_db()
        self.assertFalse(enrollment.is_active)
        self.assertIsNotNone(period.revoked_at)
        self.assertTrue(
            has_course_access(
                course_id=self.course_b.pk,
                student_id=student.pk,
                at=period.revoked_at - timedelta(microseconds=1),
            )
        )
        self.assertFalse(
            has_course_access(
                course_id=self.course_b.pk,
                student_id=student.pk,
                at=period.revoked_at,
            )
        )
        self.assertTrue(
            CourseEnrollmentAccessPeriod.objects.filter(enrollment=enrollment).exists()
        )

    def test_reenrollment_opens_a_new_access_period(self):
        student = User.objects.create_user(
            username="RETURN-1",
            student_number="RETURN-1",
            display_name="返班学生",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        from .enrollment_access import close_enrollment_access, open_enrollment_access

        open_enrollment_access(enrollment)
        closed = close_enrollment_access(enrollment, at=timezone.now())
        enrollment.is_active = False
        enrollment.save(update_fields=["is_active"])

        created_accounts, enrollments = enrollment_services.enroll_parsed_students(
            self.course_b,
            self.class_b,
            [ParsedStudent("RETURN-1", "返班学生", self.class_b.pk, str(self.class_b))],
        )

        enrollment.refresh_from_db()
        self.assertEqual((created_accounts, enrollments), (0, 1))
        self.assertTrue(enrollment.is_active)
        periods = list(enrollment.access_periods.order_by("revision"))
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0].pk, closed.pk)
        self.assertIsNone(periods[1].revoked_at)

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
        with self.assertRaisesMessage(ValidationError, "登记姓名与当前输入不一致"):
            enroll_single_student(self.course_b, self.class_b, "S3", "另一个姓名")

        CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        with self.assertRaisesMessage(ValidationError, "已在当前班级"):
            enroll_single_student(self.course_b, self.class_b, "S3", "学生丙")

    def test_manual_enrollment_rejects_an_inactive_target_class(self):
        from .enrollment_services import enroll_single_student

        self.class_b.is_active = False
        self.class_b.save(update_fields=["is_active"])

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enroll_single_student(self.course_b, self.class_b, "S4", "停用班级学生")

        self.assertFalse(User.objects.filter(student_number="S4").exists())

    def test_batch_enrollment_rejects_an_inactive_target_class(self):
        from .enrollment_services import enroll_parsed_students

        self.class_b.is_active = False
        self.class_b.save(update_fields=["is_active"])
        parsed = [ParsedStudent("S5", "批量学生", self.class_b.pk, str(self.class_b))]

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enroll_parsed_students(self.course_b, self.class_b, parsed)

        self.assertFalse(User.objects.filter(student_number="S5").exists())

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

    def test_class_copy_rejects_an_inactive_target_but_allows_an_inactive_source(self):
        from .enrollment_services import copy_class_enrollments

        student = User.objects.create_user(
            username="C3",
            student_number="C3",
            display_name="历史来源学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(course=self.course_a, class_group=self.class_a, student=student)
        self.class_b.is_active = False
        self.class_b.save(update_fields=["is_active"])

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            copy_class_enrollments(
                target_course=self.course_b,
                target_class=self.class_b,
                source_class=self.class_a,
            )

        self.class_b.is_active = True
        self.class_b.save(update_fields=["is_active"])
        self.class_a.is_active = False
        self.class_a.save(update_fields=["is_active"])
        created, skipped = copy_class_enrollments(
            target_course=self.course_b,
            target_class=self.class_b,
            source_class=self.class_a,
        )

        self.assertEqual((created, skipped), (1, 0))


class FreshClassLifecycleWriteTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="locking-teacher", role=User.Role.TEACHER)
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="来源课程",
            code="LOCK-A",
            term="2026春",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="目标课程",
            code="LOCK-B",
            term="2026春",
        )
        self.source_class = ClassGroup.objects.create(course=self.course_a, name="来源班")
        self.target_class = ClassGroup.objects.create(course=self.course_b, name="目标班")

    def _deactivate_after_read(self, class_group):
        stale_class = ClassGroup.objects.get(pk=class_group.pk)
        ClassGroup.objects.filter(pk=class_group.pk).update(is_active=False)
        return stale_class

    def test_manual_enrollment_reloads_a_stale_target_class_before_writing(self):
        stale_target = self._deactivate_after_read(self.target_class)

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enrollment_services.enroll_single_student(
                self.course_b,
                stale_target,
                "STALE-MANUAL",
                "陈旧手工学生",
            )

        self.assertFalse(User.objects.filter(student_number="STALE-MANUAL").exists())
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course_b).exists())

    def test_excel_enrollment_reloads_a_stale_target_class_before_writing(self):
        stale_target = self._deactivate_after_read(self.target_class)
        parsed_students = [
            ParsedStudent(
                "STALE-EXCEL",
                "陈旧 Excel 学生",
                stale_target.pk,
                str(stale_target),
            )
        ]

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enrollment_services.enroll_parsed_students(
                self.course_b,
                stale_target,
                parsed_students,
            )

        self.assertFalse(User.objects.filter(student_number="STALE-EXCEL").exists())
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course_b).exists())

    def test_copy_reloads_a_stale_target_class_before_writing(self):
        student = User.objects.create_user(
            username="STALE-COPY",
            student_number="STALE-COPY",
            display_name="陈旧复制学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=self.source_class,
            student=student,
        )
        stale_target = self._deactivate_after_read(self.target_class)

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enrollment_services.copy_class_enrollments(
                target_course=self.course_b,
                target_class=stale_target,
                source_class=self.source_class,
            )

        self.assertFalse(CourseEnrollment.objects.filter(course=self.course_b).exists())

    def test_transfer_reloads_a_stale_target_class_before_writing(self):
        student = User.objects.create_user(
            username="STALE-TRANSFER",
            student_number="STALE-TRANSFER",
            display_name="陈旧转班学生",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.target_class,
            student=student,
        )
        destination = ClassGroup.objects.create(course=self.course_b, name="转入目标班")
        stale_destination = self._deactivate_after_read(destination)

        with self.assertRaisesMessage(ValidationError, "目标班级已停用"):
            enrollment_services.transfer_course_enrollment(enrollment, stale_destination)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group_id, self.target_class.pk)

    def test_legacy_import_reloads_stale_target_classes_before_writing(self):
        legacy_class = ClassGroup.objects.create(owner=self.teacher, name="旧导入班")
        stale_legacy_class = self._deactivate_after_read(legacy_class)

        with self.assertRaisesMessage(StudentImportError, "班级已停用，不能导入学生"):
            enrollment_services.import_legacy_students(
                [
                    ParsedStudent(
                        "STALE-LEGACY",
                        "陈旧旧导入学生",
                        stale_legacy_class.pk,
                        str(stale_legacy_class),
                    )
                ],
                [stale_legacy_class],
            )

        self.assertFalse(User.objects.filter(student_number="STALE-LEGACY").exists())


class CourseEnrollmentLockOrderContractTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="enrollment-lock-teacher",
            role=User.Role.TEACHER,
        )
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="锁序来源课程",
            code="ENROLL-LOCK-A",
            term="2026春",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="锁序目标课程",
            code="ENROLL-LOCK-B",
            term="2026春",
        )
        self.class_a = ClassGroup.objects.create(course=self.course_a, name="锁序来源班")
        self.class_b = ClassGroup.objects.create(course=self.course_b, name="锁序目标班")

    def test_manual_enrollment_locks_course_then_class_then_user(self):
        lock_order = []
        original_course_lock = Course.objects.select_for_update
        original_class_lock = ClassGroup.objects.select_for_update
        original_create_user = User.objects.create_user

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_class(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        def create_user(*args, **kwargs):
            lock_order.append("user")
            return original_create_user(*args, **kwargs)

        with (
            patch.object(Course.objects, "select_for_update", side_effect=lock_course),
            patch.object(ClassGroup.objects, "select_for_update", side_effect=lock_class),
            patch.object(User.objects, "create_user", side_effect=create_user),
        ):
            enrollment_services.enroll_single_student(
                self.course_b,
                self.class_b,
                "LOCK-MANUAL",
                "锁序手工学生",
            )

        self.assertEqual(lock_order[:3], ["course", "class", "user"])

    def test_copy_locks_target_course_then_class_then_enrollment_write(self):
        student = User.objects.create_user(
            username="LOCK-COPY",
            student_number="LOCK-COPY",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=self.class_a,
            student=student,
        )
        lock_order = []
        original_course_lock = Course.objects.select_for_update
        original_class_lock = ClassGroup.objects.select_for_update
        original_enrollment_write = CourseEnrollment.objects.create

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_class(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        def write_enrollment(*args, **kwargs):
            lock_order.append("enrollment")
            return original_enrollment_write(*args, **kwargs)

        with (
            patch.object(Course.objects, "select_for_update", side_effect=lock_course),
            patch.object(ClassGroup.objects, "select_for_update", side_effect=lock_class),
            patch.object(
                CourseEnrollment.objects,
                "create",
                side_effect=write_enrollment,
            ),
        ):
            enrollment_services.copy_class_enrollments(
                target_course=self.course_b,
                target_class=self.class_b,
                source_class=self.class_a,
            )

        self.assertEqual(lock_order[:3], ["course", "class", "enrollment"])

    def test_transfer_locks_course_then_class_then_enrollment(self):
        student = User.objects.create_user(
            username="LOCK-TRANSFER",
            student_number="LOCK-TRANSFER",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=student,
        )
        destination = ClassGroup.objects.create(course=self.course_b, name="锁序转入班")
        lock_order = []
        original_course_lock = Course.objects.select_for_update
        original_class_lock = ClassGroup.objects.select_for_update
        original_enrollment_lock = CourseEnrollment.objects.select_for_update

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_class(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        def lock_enrollment(*args, **kwargs):
            lock_order.append("enrollment")
            return original_enrollment_lock(*args, **kwargs)

        with (
            patch.object(Course.objects, "select_for_update", side_effect=lock_course),
            patch.object(ClassGroup.objects, "select_for_update", side_effect=lock_class),
            patch.object(
                CourseEnrollment.objects,
                "select_for_update",
                side_effect=lock_enrollment,
            ),
        ):
            enrollment_services.transfer_course_enrollment(enrollment, destination)

        self.assertEqual(lock_order[:3], ["course", "class", "enrollment"])
