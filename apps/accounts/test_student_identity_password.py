import json
import uuid
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, get_commands
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from apps.audit.models import AuditLog
from .enrollment_services import enroll_single_student, remove_course_enrollment
from .models import ClassGroup, Course, CourseEnrollment, User


class StudentIdentityApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="identity-teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, code="IDENTITY", name="身份课程", term="2026")
        self.group = ClassGroup.objects.create(course=self.course, owner=self.teacher, name="目标班")
        self.student = User.objects.create_user(
            username="ID-001", student_number="ID-001", display_name="原姓名",
            role=User.Role.STUDENT, password="Personal-password-1",
        )
        self.enrollment = CourseEnrollment.objects.create(course=self.course, class_group=self.group, student=self.student)
        self.base = f"/api/v1/teacher/accounts/courses/{self.course.pk}/classes/{self.group.pk}/"
        self.client.force_login(self.teacher)

    def post(self, suffix, data):
        return self.client.post(self.base + suffix, json.dumps(data), content_type="application/json")

    def edit(self, data):
        return self.client.patch(
            self.base + f"students/{self.student.pk}/identity/", json.dumps(data), content_type="application/json",
        )

    def identity_input(self):
        response = self.client.get(self.base + "manage/")
        row = response.json()["enrollments"][0]
        self.assertIn("identity_state_token", row)
        return {"student_number": "ID-002", "display_name": "更正姓名", "state_token": row["identity_state_token"]}

    def remove(self):
        response = self.post(f"students/{self.student.pk}/remove/", {"confirmation": "delete"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_edit_preserves_internal_identity_password_and_memberships(self):
        other = Course.objects.create(owner=self.teacher, code="OTHER", name="另一课程", term="2026")
        group = ClassGroup.objects.create(course=other, name="另一班")
        second = CourseEnrollment.objects.create(course=other, class_group=group, student=self.student)
        self.student.uses_initial_password = True
        self.student.initial_password_reminder_shown_at = timezone.now()
        self.student.save()
        original_password = self.student.password
        row = self.client.get(self.base + "manage/").json()["enrollments"][0]
        self.assertTrue(row.get("affects_other_courses"))
        data = self.identity_input()
        response = self.edit(data)
        self.assertEqual(response.status_code, 200, response.content)
        self.student.refresh_from_db()
        self.assertEqual((self.student.student_number, self.student.username, self.student.display_name), ("ID-002", "ID-002", "更正姓名"))
        self.assertEqual(self.student.password, original_password)
        self.assertTrue(self.student.uses_initial_password)
        self.assertIsNotNone(self.student.initial_password_reminder_shown_at)
        self.assertEqual(set(self.student.course_enrollments.values_list("pk", flat=True)), {self.enrollment.pk, second.pk})
        self.assertEqual(self.edit(data).json()["error"]["code"], "STALE_CONFIRMATION")

    def test_edit_rejects_number_and_username_collisions(self):
        data = self.identity_input()
        User.objects.create_user(username="another-login", student_number="TAKEN", role=User.Role.STUDENT)
        User.objects.create_user(username="teacher-login", role=User.Role.TEACHER)
        for number, code in (("TAKEN", "STUDENT_NUMBER_CONFLICT"), ("teacher-login", "USERNAME_CONFLICT")):
            with self.subTest(code=code):
                response = self.edit({**data, "student_number": number})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], code)

    def test_name_only_edit_keeps_an_existing_distinct_login_name(self):
        self.student.username = "existing-login-name"
        self.student.save(update_fields=["username"])
        data = self.identity_input()
        response = self.edit({**data, "student_number": "ID-001"})
        self.assertEqual(response.status_code, 200, response.content)
        self.student.refresh_from_db()
        self.assertEqual(self.student.username, "existing-login-name")

    def test_removed_student_confirms_correction_without_password_changes(self):
        self.student.uses_initial_password = True
        self.student.save()
        password = self.student.password
        self.remove()
        data = {"student_number": "ID-001", "display_name": "正确姓名"}
        conflict = self.post("students/manual/", data)
        self.assertEqual(conflict.status_code, 409, conflict.content)
        error = conflict.json()["error"]
        self.assertEqual(error["code"], "STUDENT_NAME_CONFLICT")
        self.assertEqual(error["fields"]["existing_name"], "原姓名")
        self.assertNotIn("ErrorDetail", error["message"])
        self.student.refresh_from_db()
        self.assertEqual(self.student.display_name, "原姓名")
        self.assertFalse(self.student.course_enrollments.exists())
        response = self.post("students/manual/", {**data, "confirmation_token": error["fields"]["confirmation_token"]})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["student"]["id"], self.student.pk)
        self.student.refresh_from_db()
        self.assertEqual(self.student.display_name, "正确姓名")
        self.assertEqual(self.student.password, password)
        self.assertTrue(self.student.uses_initial_password)

    def test_unrelated_name_conflict_is_forbidden_but_same_name_reuses_account(self):
        self.enrollment.delete()
        conflict = self.post("students/manual/", {"student_number": "ID-001", "display_name": "其他姓名"})
        self.assertEqual(conflict.status_code, 403, conflict.content)
        self.assertEqual(conflict.json()["error"]["code"], "STUDENT_IDENTITY_UPDATE_FORBIDDEN")
        self.assertNotIn("confirmation_token", conflict.json()["error"].get("fields", {}))
        response = self.post("students/manual/", {"student_number": "ID-001", "display_name": "原姓名"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["student"]["id"], self.student.pk)

    def test_nonstudent_account_and_existing_enrollment_codes(self):
        response = self.post("students/manual/", {"student_number": self.teacher.username, "display_name": "老师"})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "STUDENT_ACCOUNT_TYPE_CONFLICT")
        response = self.post("students/manual/", {"student_number": "ID-001", "display_name": "原姓名"})
        self.assertEqual(response.json()["error"]["code"], "STUDENT_ALREADY_ENROLLED")
        other = ClassGroup.objects.create(course=self.course, name="另一班")
        self.enrollment.class_group = other
        self.enrollment.save()
        response = self.post("students/manual/", {"student_number": "ID-001", "display_name": "原姓名"})
        self.assertEqual(response.json()["error"]["code"], "STUDENT_ALREADY_IN_OTHER_CLASS")

    def test_confirmation_rejects_tampering_expiry_and_changed_identity(self):
        self.remove()
        data = {"student_number": "ID-001", "display_name": "正确姓名"}
        token = self.post("students/manual/", data).json()["error"]["fields"]["confirmation_token"]
        with patch("django.core.signing.time.time", return_value=99999999999):
            expired = self.post("students/manual/", {**data, "confirmation_token": token})
        self.assertEqual(expired.json()["error"]["code"], "STALE_CONFIRMATION")
        for bad in (token + "tampered",):
            response = self.post("students/manual/", {**data, "confirmation_token": bad})
            self.assertEqual(response.json()["error"]["code"], "STALE_CONFIRMATION")
        self.student.display_name = "另一教师已更正"
        self.student.save()
        response = self.post("students/manual/", {**data, "confirmation_token": token})
        self.assertEqual(response.json()["error"]["code"], "STALE_CONFIRMATION")
        self.assertFalse(self.student.course_enrollments.exists())

    def test_confirmation_is_bound_to_actor_input_role_and_membership_state(self):
        self.remove()
        data = {"student_number": "ID-001", "display_name": "正确姓名"}
        token = self.post("students/manual/", data).json()["error"]["fields"]["confirmation_token"]
        altered = self.post("students/manual/", {**data, "display_name": "篡改输入", "confirmation_token": token})
        self.assertEqual(altered.json()["error"]["code"], "STALE_CONFIRMATION")
        other_teacher = User.objects.create_user(username="other-identity-teacher", role=User.Role.TEACHER)
        self.client.force_login(other_teacher)
        altered = self.post("students/manual/", {**data, "confirmation_token": token})
        self.assertEqual(altered.json()["error"]["code"], "STALE_CONFIRMATION")
        self.client.force_login(self.teacher)
        self.group.is_active = False
        self.group.save()
        altered = self.post("students/manual/", {**data, "confirmation_token": token})
        self.assertEqual(altered.json()["error"]["code"], "STALE_CONFIRMATION")
        self.group.is_active = True
        self.group.save()
        User.objects.filter(pk=self.student.pk).update(role=User.Role.TEACHER)
        altered = self.post("students/manual/", {**data, "confirmation_token": token})
        self.assertEqual(altered.json()["error"]["code"], "STALE_CONFIRMATION")
        self.assertFalse(self.student.course_enrollments.exists())

    def test_confirmation_audit_failure_rolls_back_identity_and_enrollment(self):
        self.remove()
        data = {"student_number": "ID-001", "display_name": "正确姓名"}
        token = self.post("students/manual/", data).json()["error"]["fields"]["confirmation_token"]
        with patch("apps.accounts.student_identity.log_action", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.post("students/manual/", {**data, "confirmation_token": token})
        self.student.refresh_from_db()
        self.assertEqual(self.student.display_name, "原姓名")
        self.assertFalse(self.student.course_enrollments.exists())

    def test_password_changes_do_not_invalidate_identity_confirmation_or_get_overwritten(self):
        data = self.identity_input()
        self.student.set_password("Changed-in-another-session")
        self.student.uses_initial_password = True
        self.student.save()
        password = self.student.password
        response = self.edit(data)
        self.assertEqual(response.status_code, 200, response.content)
        self.student.refresh_from_db()
        self.assertEqual(self.student.password, password)
        self.assertTrue(self.student.uses_initial_password)

    def test_new_other_course_membership_requires_a_new_cross_course_confirmation(self):
        data = self.identity_input()
        other = Course.objects.create(owner=self.teacher, code="NEW-OTHER", name="新课程", term="2026")
        group = ClassGroup.objects.create(course=other, name="新班级")
        CourseEnrollment.objects.create(course=other, class_group=group, student=self.student)
        response = self.edit(data)
        self.assertEqual(response.json()["error"]["code"], "STALE_CONFIRMATION")
        self.student.refresh_from_db()
        self.assertEqual(self.student.display_name, "原姓名")

    def test_other_course_history_still_requires_global_identity_notice(self):
        other = Course.objects.create(owner=self.teacher, code="HISTORY-OTHER", name="历史课程", term="2026")
        group = ClassGroup.objects.create(course=other, name="历史班级")
        CourseEnrollment.objects.create(course=other, class_group=group, student=self.student)
        url = f"/api/v1/teacher/accounts/courses/{other.pk}/classes/{group.pk}/students/{self.student.pk}/remove/"
        self.assertEqual(self.client.post(url, json.dumps({"confirmation": "delete"}), content_type="application/json").status_code, 200)
        row = self.client.get(self.base + "manage/").json()["enrollments"][0]
        self.assertTrue(row["affects_other_courses"])

    def test_global_creation_rolls_back_if_audit_fails(self):
        with patch("apps.accounts.api.teacher_account_management_views.log_action", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/v1/teacher/accounts/students/", json.dumps({"student_number": "CREATE-AUDIT", "display_name": "学生", "class_group": self.group.pk}), content_type="application/json")
        self.assertFalse(User.objects.filter(student_number="CREATE-AUDIT").exists())

    def test_global_creation_race_returns_business_conflict(self):
        from .forms import StudentCreateForm
        validate = StudentCreateForm.is_valid

        def concurrent_creation(form):
            valid = validate(form)
            if valid:
                User.objects.create_user(username="CREATE-RACE", student_number="CREATE-RACE", role=User.Role.STUDENT)
            return valid

        self.client.raise_request_exception = False
        with patch.object(StudentCreateForm, "is_valid", concurrent_creation):
            response = self.client.post("/api/v1/teacher/accounts/students/", json.dumps({"student_number": "CREATE-RACE", "display_name": "学生", "class_group": self.group.pk}), content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "STUDENT_NUMBER_CONFLICT")
        self.assertEqual(User.objects.filter(student_number="CREATE-RACE").count(), 1)

    def test_history_service_uses_stable_user_id_and_course_scope(self):
        from .membership_history import has_course_membership_history
        self.assertTrue(has_course_membership_history(self.course.pk, self.student.pk))
        self.remove()
        self.assertTrue(has_course_membership_history(self.course.pk, self.student.pk))
        User.objects.filter(pk=self.student.pk).update(student_number="RENAMED", username="RENAMED")
        self.assertTrue(has_course_membership_history(self.course.pk, self.student.pk))
        self.assertFalse(has_course_membership_history(self.course.pk + 999, self.student.pk))

    def test_identity_edit_and_reenrollment_preserve_research_records(self):
        from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment, SubmissionEvent
        from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
        from apps.experiments.presentation_instances import get_or_create_default_item_instance
        from apps.question_bank.models import Question, QuestionBank
        bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name="历史题库")
        question = Question.objects.create(bank=bank, sequence=1, question_type=Question.Type.SHORT, text="历史题目", correct_answer="答案")
        experiment = Experiment.objects.create(course=self.course, owner=self.teacher, question_bank=bank, name="历史实验", status=Experiment.Status.PUBLISHED)
        version = ExperimentVersion.objects.create(experiment=experiment, version_number=1, published_by=self.teacher, configuration={})
        snapshot = QuestionSnapshot.objects.create(version=version, source_question=question, source_sequence=1, code=question.code, question_type=question.question_type, text=question.text, correct_answer=question.correct_answer, primary_kc="历史 KC")
        session = ExperimentSession.objects.create(version=version, student=self.student, class_snapshot_name="历史班级")
        assignment = StudentQuestionAssignment.objects.create(session=session, snapshot=snapshot, item_instance=get_or_create_default_item_instance(snapshot), assigned_sequence_index=1)
        attempt = Attempt.objects.create(session=session, assignment=assignment)
        submission = SubmissionEvent.objects.create(attempt=attempt, submission_token=uuid.uuid4(), final_answer="历史作答", server_received_at=timezone.now(), committed_at=timezone.now())
        before = {"snapshot": snapshot.text, "submission": submission.final_answer, "class": session.class_snapshot_name}
        self.assertEqual(self.edit(self.identity_input()).status_code, 200)
        self.remove()
        conflict = self.post("students/manual/", {"student_number": "ID-002", "display_name": "再次更正"})
        response = self.post("students/manual/", {"student_number": "ID-002", "display_name": "再次更正", "confirmation_token": conflict.json()["error"]["fields"]["confirmation_token"]})
        self.assertEqual(response.status_code, 201)
        session.refresh_from_db()
        submission.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(session.student_id, self.student.pk)
        self.assertEqual(submission.attempt_id, attempt.pk)
        self.assertEqual({"snapshot": snapshot.text, "submission": submission.final_answer, "class": session.class_snapshot_name}, before)


class StudentPasswordTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="password-teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, code="PW", name="密码课程", term="2026")
        self.group = ClassGroup.objects.create(course=self.course, owner=self.teacher, name="班级")
        self.student, _ = enroll_single_student(self.course, self.group, "PW-001", "学生")

    def test_new_student_has_explicit_initial_password_state_and_pending_reminder(self):
        self.assertTrue(self.student.check_password("SPW-001"))
        self.assertTrue(self.student.uses_initial_password)
        self.assertIsNone(self.student.initial_password_reminder_shown_at)
        self.assertIsNotNone(self.student.initial_password_state_recorded_at)

    def test_initial_password_is_a_reminder_and_never_blocks_student_pages(self):
        self.client.login(username="PW-001", password="SPW-001")
        session = self.client.get("/api/v1/auth/session/").json()["user"]
        self.assertEqual(session["home_url"], "/student/")
        self.assertTrue(session["uses_initial_password"])
        self.assertTrue(session["initial_password_reminder_pending"])
        self.assertNotIn("must_change_password", session)
        self.assertEqual(self.client.get("/api/v1/student/courses/").status_code, 200)
        response = self.client.get("/student/")
        self.assertNotEqual(getattr(response, "url", None), "/password/change/")

    def test_reminder_confirmation_is_idempotent_and_only_students_can_use_it(self):
        self.client.force_login(self.student)
        url = "/api/v1/auth/password/reminder-shown/"
        first = self.client.post(url, "{}", content_type="application/json")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertFalse(first.json()["user"]["initial_password_reminder_pending"])
        self.student.refresh_from_db()
        shown_at = self.student.initial_password_reminder_shown_at
        self.assertIsNotNone(shown_at)
        second = self.client.post(url, "{}", content_type="application/json")
        self.assertEqual(second.status_code, 200, second.content)
        self.student.refresh_from_db()
        self.assertEqual(self.student.initial_password_reminder_shown_at, shown_at)
        self.client.force_login(self.teacher)
        denied = self.client.post(url, "{}", content_type="application/json")
        self.assertEqual(denied.status_code, 403)

    def test_failed_reminder_confirmation_does_not_mark_the_reminder_as_shown(self):
        self.client.force_login(self.student)
        self.client.raise_request_exception = False
        with patch("apps.accounts.password_services.User.save", side_effect=RuntimeError("database unavailable")):
            failed = self.client.post(
                "/api/v1/auth/password/reminder-shown/", "{}", content_type="application/json"
            )
        self.assertEqual(failed.status_code, 500)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.initial_password_reminder_shown_at)
        retry = self.client.post(
            "/api/v1/auth/password/reminder-shown/", "{}", content_type="application/json"
        )
        self.assertEqual(retry.status_code, 200, retry.content)

    def test_password_change_is_permanent_and_validates_the_current_password(self):
        self.client.login(username="PW-001", password="SPW-001")
        invalid = self.client.post(
            "/api/v1/auth/password/change/",
            json.dumps({
                "current_password": "wrong-password",
                "new_password1": "Strong-password-2",
                "new_password2": "Strong-password-2",
            }),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.student.refresh_from_db()
        self.assertTrue(self.student.uses_initial_password)
        self.assertIsNone(self.student.initial_password_reminder_shown_at)
        changed = self.client.post(
            "/api/v1/auth/password/change/",
            json.dumps({
                "current_password": "SPW-001",
                "new_password1": "Strong-password-2",
                "new_password2": "Strong-password-2",
            }),
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertFalse(changed.json()["user"]["uses_initial_password"])
        self.assertFalse(changed.json()["user"]["initial_password_reminder_pending"])
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("Strong-password-2"))
        self.assertFalse(self.student.uses_initial_password)
        self.assertEqual(self.client.get("/api/v1/auth/session/").status_code, 200)

    def test_student_number_change_never_changes_password_or_explicit_initial_state(self):
        self.student.initial_password_reminder_shown_at = timezone.now()
        self.student.save(update_fields=["initial_password_reminder_shown_at"])
        original_password = self.student.password
        self.client.force_login(self.teacher)
        base = f"/api/v1/teacher/accounts/courses/{self.course.pk}/classes/{self.group.pk}/"
        token = self.client.get(base + "manage/").json()["enrollments"][0]["identity_state_token"]
        edited = self.client.patch(
            base + f"students/{self.student.pk}/identity/",
            json.dumps({
                "student_number": "PW-0002",
                "display_name": "学生",
                "state_token": token,
            }),
            content_type="application/json",
        )
        self.assertEqual(edited.status_code, 200, edited.content)
        self.student.refresh_from_db()
        self.assertEqual(self.student.password, original_password)
        self.assertTrue(self.student.uses_initial_password)
        self.assertIsNotNone(self.student.initial_password_reminder_shown_at)

    def test_teacher_reset_uses_current_number_and_reopens_the_reminder(self):
        self.student.set_password("Personal-password-1")
        self.student.uses_initial_password = False
        self.student.initial_password_reminder_shown_at = timezone.now()
        self.student.save()
        old = Client()
        old.login(username="PW-001", password="Personal-password-1")
        self.client.force_login(self.teacher)
        base = f"/api/v1/teacher/accounts/courses/{self.course.pk}/classes/{self.group.pk}/"
        token = self.client.get(base + "manage/").json()["enrollments"][0]["identity_state_token"]
        edited = self.client.patch(base + f"students/{self.student.pk}/identity/", json.dumps({"student_number": "PW-002", "display_name": "学生", "state_token": token}), content_type="application/json")
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(old.get("/api/v1/auth/session/").status_code, 200)
        url = f"/api/v1/teacher/accounts/courses/{self.course.pk}/classes/{self.group.pk}/students/{self.student.pk}/reset-password/"
        response = self.client.post(url, json.dumps({"confirmation": "reset"}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("SPW-002"))
        self.assertTrue(self.student.uses_initial_password)
        self.assertIsNone(self.student.initial_password_reminder_shown_at)
        self.assertIsNotNone(self.student.initial_password_state_recorded_at)
        self.assertEqual(old.get("/api/v1/auth/session/").status_code, 401)

    def test_inflight_password_change_cannot_overwrite_a_teacher_reset(self):
        from .password_services import change_student_password, reset_student_initial_password
        from .student_identity import StudentIdentityError
        self.client.login(username="PW-001", password="SPW-001")
        inflight = RequestFactory().post("/api/v1/auth/password/change/")
        inflight.user = User.objects.get(pk=self.student.pk)
        inflight.session = self.client.session
        teacher_request = RequestFactory().post("/")
        teacher_request.user = self.teacher
        reset_student_initial_password(request=teacher_request, student_id=self.student.pk, course_id=self.course.pk, class_id=self.group.pk)
        self.student.refresh_from_db()
        reset_hash = self.student.password
        with self.assertRaises(StudentIdentityError) as blocked:
            change_student_password(request=inflight, data={"new_password1": "Obsolete-request-password", "new_password2": "Obsolete-request-password"})
        self.assertEqual(blocked.exception.business_code, "AUTHENTICATION_REQUIRED")
        self.student.refresh_from_db()
        self.assertEqual(self.student.password, reset_hash)
        self.assertTrue(self.student.uses_initial_password)

    def test_default_password_command_is_one_time_and_never_changes_hashes(self):
        self.assertIn("mark_student_default_passwords", get_commands())
        User.objects.filter(pk=self.student.pk).update(
            uses_initial_password=False,
            initial_password_reminder_shown_at=None,
            initial_password_state_recorded_at=None,
        )
        original = self.student.password
        personal = User.objects.create_user(
            username="CUSTOM", student_number="CUSTOM", password="Personal-password-1",
            role=User.Role.STUDENT,
        )
        User.objects.filter(pk=personal.pk).update(initial_password_state_recorded_at=None)
        absent = User.objects.create_user(username="NO-NUMBER", role=User.Role.STUDENT)
        User.objects.filter(pk=absent.pk).update(initial_password_state_recorded_at=None)
        before = dict(User.objects.values_list("pk", "password"))
        out = StringIO()
        call_command("mark_student_default_passwords", stdout=out)
        preview = json.loads(out.getvalue())
        self.assertEqual(preview["mode"], "dry-run")
        self.assertEqual(preview["matched_initial_password"], 1)
        self.assertEqual(preview["personal_or_unrecognized_password"], 2)
        self.assertEqual(preview["would_record"], 3)
        self.student.refresh_from_db()
        self.assertFalse(self.student.uses_initial_password)
        self.assertIsNone(self.student.initial_password_state_recorded_at)
        self.assertNotIn(original, out.getvalue())
        call_command("mark_student_default_passwords", apply=True, stdout=StringIO())
        self.student.refresh_from_db()
        personal.refresh_from_db()
        absent.refresh_from_db()
        self.assertTrue(self.student.uses_initial_password)
        self.assertFalse(personal.uses_initial_password)
        self.assertFalse(absent.uses_initial_password)
        self.assertTrue(all(user.initial_password_state_recorded_at for user in (self.student, personal, absent)))
        again = StringIO()
        call_command("mark_student_default_passwords", apply=True, stdout=again)
        self.student.refresh_from_db()
        self.assertEqual(json.loads(again.getvalue())["already_authoritative"], 3)
        self.assertEqual(json.loads(again.getvalue())["recorded"], 0)
        self.assertEqual(self.student.password, original)

    def test_password_change_requires_csrf_and_legacy_login_post_is_retired(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.student)
        response = client.post(
            "/api/v1/auth/password/change/",
            json.dumps({
                "current_password": "SPW-001",
                "new_password1": "New-password-2",
                "new_password2": "New-password-2",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "CSRF_FAILED")
        response = self.client.post("/login/?next=/student/", {"username": "PW-001", "password": "SPW-001"})
        self.assertEqual(response.status_code, 405)
