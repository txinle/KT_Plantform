from concurrent.futures import ThreadPoolExecutor
import json
from io import StringIO
from threading import Barrier, Event
from unittest.mock import patch
from unittest import skipUnless

from django.db import OperationalError, close_old_connections, connection, transaction
from django.core.management import call_command
from django.test import RequestFactory, TransactionTestCase

from apps.audit.services import log_action
from .enrollment_services import remove_course_enrollment
from .models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User
from .student_identity import StudentIdentityError, enroll_student, identity_state_token, update_student_identity


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class StudentIdentityConcurrencyTests(TransactionTestCase):
    def test_mark_command_commit_failure_is_counted_as_error_not_success(self):
        student = User.objects.create_user(username="COMMAND-FAIL", student_number="COMMAND-FAIL", password="SCOMMAND-FAIL", role=User.Role.STUDENT)
        password = student.password
        output = StringIO()
        with patch.object(connection, "commit", side_effect=OperationalError("commit failed")):
            call_command("mark_student_default_passwords", apply=True, stdout=output, stderr=StringIO())
        counts = json.loads(output.getvalue())
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["matched"], 0)
        self.assertEqual(counts["marked"], 0)
        student.refresh_from_db()
        self.assertFalse(student.must_change_password)
        self.assertEqual(student.password, password)

    def test_collaborator_revocation_waits_for_inflight_identity_transaction(self):
        from . import student_identity
        owner = User.objects.create_user(username="lock-owner", role=User.Role.TEACHER)
        teacher = User.objects.create_user(username="lock-collaborator", role=User.Role.TEACHER)
        course = Course.objects.create(owner=owner, code="PERMISSION-LOCK", name="权限课程", term="2026")
        link = CourseTeacher.objects.create(course=course, teacher=teacher)
        group = ClassGroup.objects.create(course=course, name="班级")
        student = User.objects.create_user(username="PERMISSION-STUDENT", student_number="PERMISSION-STUDENT", role=User.Role.STUDENT)
        CourseEnrollment.objects.create(course=course, class_group=group, student=student)
        token = identity_state_token(student, course, group, teacher)
        reached_audit, release = Event(), Event()
        audit = student_identity._audit_identity

        def pause_audit(*args):
            reached_audit.set()
            if not release.wait(10):
                raise RuntimeError("test barrier timeout")
            return audit(*args)

        def edit():
            close_old_connections()
            try:
                request = RequestFactory().post("/")
                request.user = User.objects.get(pk=teacher.pk)
                update_student_identity(request=request, course_id=course.pk, class_id=group.pk, student_id=student.pk, student_number=student.student_number, display_name="已更正", state_token=token)
            finally:
                close_old_connections()

        with patch.object(student_identity, "_audit_identity", pause_audit), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit)
            try:
                self.assertTrue(reached_audit.wait(10))
                with self.assertRaises(OperationalError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '250ms'")
                        CourseTeacher.objects.filter(pk=link.pk).delete()
            finally:
                release.set()
            future.result(timeout=10)
        self.assertTrue(CourseTeacher.objects.filter(pk=link.pk).exists())
        self.assertEqual(User.objects.get(pk=student.pk).display_name, "已更正")

    def test_two_confirmations_cannot_overwrite_or_create_duplicate_memberships(self):
        teacher = User.objects.create_user(username="concurrent-identity-teacher", role=User.Role.TEACHER)
        course = Course.objects.create(owner=teacher, code="IDENTITY-LOCK", name="并发课程", term="2026")
        group = ClassGroup.objects.create(course=course, name="班级")
        student = User.objects.create_user(username="LOCK-001", student_number="LOCK-001", display_name="原姓名", role=User.Role.STUDENT, password="Keep-this-password")
        enrollment = CourseEnrollment.objects.create(course=course, class_group=group, student=student)
        request = RequestFactory().post("/")
        request.user = teacher
        details = remove_course_enrollment(course=course, class_group=group, enrollment=enrollment)
        log_action(request, "student.course_remove", student, details)
        with self.assertRaises(StudentIdentityError) as conflict:
            enroll_student(course, group, "LOCK-001", "更正姓名", request=request)
        token = conflict.exception.fields["confirmation_token"]
        ready = Barrier(2)

        def confirm():
            close_old_connections()
            try:
                actor = User.objects.get(pk=teacher.pk)
                request = RequestFactory().post("/")
                request.user = actor
                ready.wait(timeout=10)
                try:
                    enroll_student(course.pk, group.pk, "LOCK-001", "更正姓名", request=request, confirmation_token=token)
                    return "success"
                except StudentIdentityError as exc:
                    return exc.business_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: confirm(), range(2)))
        self.assertCountEqual(results, ["success", "STALE_CONFIRMATION"])
        self.assertEqual(CourseEnrollment.objects.filter(course=course, student=student).count(), 1)
        student.refresh_from_db()
        self.assertEqual(student.display_name, "更正姓名")
        self.assertTrue(student.check_password("Keep-this-password"))
