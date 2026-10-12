from threading import Event, Thread
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import RequestFactory, TransactionTestCase

from .class_management_services import update_course_class_group
from .forms import class_group_state_token
from .models import ClassGroup, Course, CourseEnrollment, StudentImportJob, User


@skipUnless(connection.vendor == "postgresql", "PostgreSQL locking test")
class StudentImportJobPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="student-import-concurrency-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="并发名单导入",
            code="IMPORT-CONCURRENCY",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            owner=self.teacher,
            course=self.course,
            name="并发班",
            academic_year="2026",
        )

    def test_class_update_finishes_while_password_hashing_is_paused(self):
        original_token = class_group_state_token(self.class_group)
        job = StudentImportJob.objects.create(
            preview_id=uuid.uuid4(),
            course=self.course,
            class_group=self.class_group,
            created_by=self.teacher,
            class_state_token=original_token,
            rows=[{
                "student_number": "CONCURRENT-001",
                "display_name": "并发学生",
                "class_id": self.class_group.pk,
                "class_name": str(self.class_group),
            }],
            total_rows=1,
        )
        hash_started = Event()
        release_hash = Event()
        update_finished = Event()
        errors = []

        def slow_hash(raw):
            hash_started.set()
            if not release_hash.wait(5):
                raise RuntimeError("password hash test barrier timed out")
            return f"encoded:{raw}"

        def run_worker():
            close_old_connections()
            try:
                with patch(
                    "apps.accounts.student_import_jobs.make_password",
                    side_effect=slow_hash,
                ):
                    call_command("run_student_import_jobs", once=True)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)
            finally:
                close_old_connections()

        def update_class():
            close_old_connections()
            try:
                teacher = User.objects.get(pk=self.teacher.pk)
                course = Course.objects.get(pk=self.course.pk)
                class_group = ClassGroup.objects.get(pk=self.class_group.pk)
                request = RequestFactory().patch("/")
                request.user = teacher
                saved, form = update_course_class_group(
                    request=request,
                    course=course,
                    class_group=class_group,
                    form_data={
                        "name": "并发修改后的班级",
                        "academic_year": "2026",
                        "is_active": True,
                        "state_token": original_token,
                    },
                )
                if saved is None:
                    errors.append(AssertionError(form.errors.as_text()))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)
            finally:
                update_finished.set()
                close_old_connections()

        worker = Thread(target=run_worker)
        worker.start()
        self.assertTrue(hash_started.wait(5), "worker did not reach password hashing")
        updater = Thread(target=update_class)
        updater.start()
        try:
            self.assertTrue(
                update_finished.wait(2),
                "class update was blocked by password hashing",
            )
        finally:
            release_hash.set()
            updater.join(5)
            worker.join(5)

        self.assertFalse(updater.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        job.refresh_from_db()
        self.assertEqual(job.status, StudentImportJob.Status.FAILED)
        self.assertEqual(job.error_code, "IMPORT_PREVIEW_STALE")
        self.assertFalse(CourseEnrollment.objects.filter(course=self.course).exists())
