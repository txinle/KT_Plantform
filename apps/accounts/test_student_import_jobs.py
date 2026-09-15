import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.audit.models import AuditLog

from .forms import class_group_state_token
from .models import ClassGroup, Course, CourseEnrollment, StudentImportJob, User


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
