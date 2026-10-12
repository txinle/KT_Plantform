import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.audit.models import AuditLog

from .enrollment_access import open_enrollment_access
from .enrollment_services import transfer_course_enrollment
from .forms import class_group_state_token
from .models import (
    ClassGroup, Course, CourseEnrollment, CourseEnrollmentAccessPeriod,
    StudentImportJob, User,
)
from .student_import_jobs import StudentImportJobError


class StudentImportJobTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="student-import-worker-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="后台名单导入",
            code="IMPORT-JOB",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            owner=self.teacher,
            course=self.course,
            name="导入班",
            academic_year="2026",
        )

    def create_job(self, rows):
        return StudentImportJob.objects.create(
            preview_id=uuid.uuid4(),
            course=self.course,
            class_group=self.class_group,
            created_by=self.teacher,
            class_state_token=class_group_state_token(self.class_group),
            rows=rows,
            total_rows=len(rows),
        )

    def test_worker_reuses_existing_account_and_finishes_atomic_import(self):
        existing = User.objects.create_user(
            username="IMPORT-EXISTING",
            student_number="IMPORT-EXISTING",
            display_name="已有学生",
            role=User.Role.STUDENT,
            password="preserved-password",
        )
        job = self.create_job(
            [
                {
                    "student_number": "IMPORT-EXISTING",
                    "display_name": "已有学生",
                    "class_id": self.class_group.pk,
                    "class_name": str(self.class_group),
                },
                {
                    "student_number": "IMPORT-NEW",
                    "display_name": "新建学生",
                    "class_id": self.class_group.pk,
                    "class_name": str(self.class_group),
                },
            ]
        )

        call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        existing.refresh_from_db()
        created = User.objects.get(student_number="IMPORT-NEW")
        self.assertEqual(job.status, StudentImportJob.Status.SUCCEEDED)
        self.assertEqual(job.created_accounts, 1)
        self.assertEqual(job.enrollments, 2)
        self.assertEqual(job.rows, [])
        self.assertTrue(existing.check_password("preserved-password"))
        self.assertTrue(created.check_password("SIMPORT-NEW"))
        self.assertEqual(
            CourseEnrollment.objects.filter(course=self.course).count(), 2
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="student.batch_import", object_id=str(self.class_group.pk)
            ).exists()
        )

    def test_worker_rejects_job_when_class_changed_after_queueing(self):
        job = self.create_job(
            [
                {
                    "student_number": "IMPORT-STALE",
                    "display_name": "过期学生",
                    "class_id": self.class_group.pk,
                    "class_name": str(self.class_group),
                }
            ]
        )
        self.class_group.name = "修改后的班级"
        self.class_group.save(update_fields=["name"])

        call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        self.assertEqual(job.status, StudentImportJob.Status.FAILED)
        self.assertEqual(job.error_code, "IMPORT_PREVIEW_STALE")
        self.assertIn("重新上传", job.error_message)
        self.assertEqual(job.rows, [])
        self.assertFalse(User.objects.filter(student_number="IMPORT-STALE").exists())
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course).exists())

    def test_worker_imports_174_students_without_hashing_inside_the_request(self):
        rows = [
            {
                "student_number": f"WORKER-{index:03d}",
                "display_name": f"后台学生{index:03d}",
                "class_id": self.class_group.pk,
                "class_name": str(self.class_group),
            }
            for index in range(174)
        ]
        job = self.create_job(rows)

        with patch(
            "apps.accounts.student_import_jobs.make_password",
            side_effect=lambda raw: f"encoded:{raw}",
        ) as make_password:
            call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        self.assertEqual(make_password.call_count, 174)
        self.assertEqual(job.status, StudentImportJob.Status.SUCCEEDED)
        self.assertEqual(job.created_accounts, 174)
        self.assertEqual(job.enrollments, 174)
        self.assertEqual(CourseEnrollment.objects.filter(course=self.course).count(), 174)

    def test_worker_transfers_existing_enrollment_and_skips_target_member(self):
        source_class = ClassGroup.objects.create(
            owner=self.teacher, course=self.course, name="来源班", academic_year="2026"
        )
        moving = User.objects.create_user(
            username="MOVE-001", student_number="MOVE-001",
            display_name="转班学生", role=User.Role.STUDENT,
            password="preserved-password",
        )
        already_here = User.objects.create_user(
            username="HERE-001", student_number="HERE-001",
            display_name="本班学生", role=User.Role.STUDENT,
        )
        original = CourseEnrollment.objects.create(
            course=self.course, class_group=source_class, student=moving
        )
        open_enrollment_access(original)
        existing_target = CourseEnrollment.objects.create(
            course=self.course, class_group=self.class_group, student=already_here
        )
        job = self.create_job([
            {
                "student_number": "MOVE-001", "display_name": "转班学生",
                "class_id": self.class_group.pk, "class_name": str(self.class_group),
                "source_class_id": source_class.pk,
            },
            {
                "student_number": "HERE-001", "display_name": "本班学生",
                "class_id": self.class_group.pk, "class_name": str(self.class_group),
                "source_class_id": self.class_group.pk,
            },
            {
                "student_number": "NEW-001", "display_name": "新学生",
                "class_id": self.class_group.pk, "class_name": str(self.class_group),
                "source_class_id": None,
            },
        ])

        call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        original.refresh_from_db()
        moving.refresh_from_db()
        self.assertEqual(job.status, StudentImportJob.Status.SUCCEEDED)
        self.assertEqual(job.enrollments, 1)
        self.assertEqual(job.transferred, 1)
        self.assertEqual(job.skipped, 1)
        self.assertEqual(job.created_accounts, 1)
        self.assertEqual(original.class_group_id, self.class_group.pk)
        self.assertEqual(
            CourseEnrollment.objects.get(course=self.course, student=moving).pk,
            original.pk,
        )
        self.assertEqual(
            CourseEnrollment.objects.get(course=self.course, student=already_here).pk,
            existing_target.pk,
        )
        self.assertTrue(moving.check_password("preserved-password"))
        periods = list(
            CourseEnrollmentAccessPeriod.objects.filter(enrollment=original)
            .order_by("revision")
        )
        self.assertEqual([periods[0].class_group_id, periods[1].class_group_id],
                         [source_class.pk, self.class_group.pk])
        self.assertIsNotNone(periods[0].revoked_at)
        self.assertIsNone(periods[1].revoked_at)
        self.assertTrue(AuditLog.objects.filter(
            action="student.class_transfer", object_id=str(original.pk)
        ).exists())

    def test_worker_rejects_changed_source_class_without_partial_transfer(self):
        source_class = ClassGroup.objects.create(
            owner=self.teacher, course=self.course, name="来源班", academic_year="2026"
        )
        another_class = ClassGroup.objects.create(
            owner=self.teacher, course=self.course, name="后来转入班", academic_year="2026"
        )
        moving = User.objects.create_user(
            username="MOVE-STALE", student_number="MOVE-STALE",
            display_name="转班学生", role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course, class_group=source_class, student=moving
        )
        job = self.create_job([
            {
                "student_number": "MOVE-STALE", "display_name": "转班学生",
                "class_id": self.class_group.pk, "class_name": str(self.class_group),
                "source_class_id": source_class.pk,
            },
            {
                "student_number": "NEW-STALE", "display_name": "新学生",
                "class_id": self.class_group.pk, "class_name": str(self.class_group),
                "source_class_id": None,
            },
        ])
        enrollment.class_group = another_class
        enrollment.save(update_fields=["class_group"])

        call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual(job.status, StudentImportJob.Status.FAILED)
        self.assertEqual(job.error_code, "IMPORT_PREVIEW_STALE")
        self.assertEqual(enrollment.class_group_id, another_class.pk)
        self.assertFalse(User.objects.filter(student_number="NEW-STALE").exists())

    def test_worker_rolls_back_all_transfers_when_a_later_transfer_fails(self):
        source_class = ClassGroup.objects.create(
            owner=self.teacher, course=self.course, name="来源班", academic_year="2026"
        )
        students = [
            User.objects.create_user(
                username=f"ROLLBACK-{index}", student_number=f"ROLLBACK-{index}",
                display_name=f"学生{index}", role=User.Role.STUDENT,
            )
            for index in (1, 2)
        ]
        enrollments = [
            CourseEnrollment.objects.create(
                course=self.course, class_group=source_class, student=student
            )
            for student in students
        ]
        for enrollment in enrollments:
            open_enrollment_access(enrollment)
        job = self.create_job([
            {
                "student_number": student.student_number,
                "display_name": student.display_name,
                "class_id": self.class_group.pk,
                "class_name": str(self.class_group),
                "source_class_id": source_class.pk,
            }
            for student in students
        ])

        def fail_second_transfer(enrollment, target_class):
            if enrollment.student_id == students[1].pk:
                raise StudentImportJobError("后续转班失败")
            return transfer_course_enrollment(enrollment, target_class)

        with patch(
            "apps.accounts.student_import_jobs.transfer_course_enrollment",
            side_effect=fail_second_transfer,
        ):
            call_command("run_student_import_jobs", once=True)

        job.refresh_from_db()
        self.assertEqual(job.status, StudentImportJob.Status.FAILED)
        for enrollment in enrollments:
            enrollment.refresh_from_db()
            self.assertEqual(enrollment.class_group_id, source_class.pk)
            periods = list(enrollment.access_periods.all())
            self.assertEqual(len(periods), 1)
            self.assertIsNone(periods[0].revoked_at)
