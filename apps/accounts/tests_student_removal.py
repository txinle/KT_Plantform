import json
from unittest.mock import patch

from django.test import Client, TestCase

from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.audit.models import AuditLog
from apps.experiments.models import (
    Experiment,
    ExperimentParticipantOverride,
    ExperimentStudent,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User


class StudentRemovalTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="student-remove-owner",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="student-remove-collaborator",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="student-remove-outsider",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.admin = User.objects.create_user(
            username="student-remove-admin",
            password="password123",
            role=User.Role.ADMIN,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="学生移出课程",
            code="REMOVE101",
            term="2026秋",
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="电子信息工程一班",
            academic_year="2025",
        )
        self.student = User.objects.create_user(
            username="REMOVE-S1",
            student_number="REMOVE-S1",
            display_name="待移出学生",
            role=User.Role.STUDENT,
        )
        self.enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            name="学生移出题库",
        )

    def removal_url(self, *, course=None, class_group=None, student=None):
        return (
            f"/teacher/courses/{(course or self.course).pk}/classes/"
            f"{(class_group or self.class_group).pk}/students/"
            f"{(student or self.student).pk}/remove/"
        )

    def test_removal_deletes_only_the_course_enrollment_and_cleans_draft_rules(self):
        other_course = Course.objects.create(
            owner=self.owner,
            name="保留课程",
            code="KEEP101",
            term="2026秋",
        )
        other_class = ClassGroup.objects.create(
            course=other_course,
            name="保留班级",
            academic_year="2025",
        )
        other_enrollment = CourseEnrollment.objects.create(
            course=other_course,
            class_group=other_class,
            student=self.student,
        )
        draft = Experiment.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            question_bank=self.bank,
            name="待更新草稿",
        )
        published = Experiment.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            question_bank=self.bank,
            name="已发布实验",
            status=Experiment.Status.PUBLISHED,
        )
        draft_student = ExperimentStudent.objects.create(
            experiment=draft,
            student=self.student,
            class_snapshot_name="2025 电子信息工程一班",
        )
        draft_override = ExperimentParticipantOverride.objects.create(
            experiment=draft,
            student=self.student,
            mode=ExperimentParticipantOverride.Mode.INCLUDE,
        )
        published_student = ExperimentStudent.objects.create(
            experiment=published,
            student=self.student,
            class_snapshot_name="2025 电子信息工程一班",
        )
        self.client.force_login(self.collaborator)

        response = self.client.post(
            self.removal_url(),
            {"confirmation": "delete"},
        )

        self.assertRedirects(
            response,
            f"/teacher/courses/{self.course.pk}/classes/{self.class_group.pk}/",
        )
        self.assertFalse(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertTrue(CourseEnrollment.objects.filter(pk=other_enrollment.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())
        self.assertFalse(ExperimentStudent.objects.filter(pk=draft_student.pk).exists())
        self.assertFalse(
            ExperimentParticipantOverride.objects.filter(pk=draft_override.pk).exists()
        )
        self.assertTrue(ExperimentStudent.objects.filter(pk=published_student.pk).exists())
        audit = AuditLog.objects.get(
            action="student.course_remove",
            object_id=str(self.student.pk),
        )
        self.assertEqual(audit.details["enrollment_id"], self.enrollment.pk)
        self.assertEqual(audit.details["draft_experiment_ids"], [draft.pk])

    def test_removal_preserves_published_snapshots_sessions_and_attempts(self):
        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="保留研究数据？",
            correct_answer="是",
        )
        published = Experiment.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            question_bank=self.bank,
            name="保留研究数据实验",
            status=Experiment.Status.PUBLISHED,
        )
        ExperimentStudent.objects.create(experiment=published, student=self.student)
        version = ExperimentVersion.objects.create(
            experiment=published,
            version_number=1,
            published_by=self.owner,
            configuration={},
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code=question.code,
            question_type=question.question_type,
            text=question.text,
            correct_answer=question.correct_answer,
            primary_kc="研究数据 KC",
        )
        session = ExperimentSession.objects.create(
            version=version,
            student=self.student,
            class_snapshot_name="2025 电子信息工程一班",
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(session=session, assignment=assignment)
        self.client.force_login(self.owner)

        self.client.post(self.removal_url(), {"confirmation": "delete"})

        self.assertTrue(ExperimentVersion.objects.filter(pk=version.pk).exists())
        self.assertTrue(QuestionSnapshot.objects.filter(pk=snapshot.pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=session.pk).exists())
        self.assertTrue(StudentQuestionAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(Attempt.objects.filter(pk=attempt.pk).exists())

        self.client.force_login(self.student)
        blocked_write = self.client.post(
            f"/student/attempts/{attempt.pk}/draft/",
            data=json.dumps({"answer": "不应保存"}),
            content_type="application/json",
        )
        self.assertEqual(blocked_write.status_code, 409)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)

    def test_removal_requires_confirmation_and_csrf(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.removal_url())

        self.assertEqual(response.status_code, 400)
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        csrf_response = csrf_client.post(
            self.removal_url(),
            {"confirmation": "delete"},
        )
        self.assertEqual(csrf_response.status_code, 403)
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())

    def test_owner_collaborator_and_admin_can_remove_but_outsider_gets_404(self):
        for index, actor in enumerate((self.owner, self.collaborator, self.admin), start=1):
            with self.subTest(actor=actor.username):
                student = User.objects.create_user(
                    username=f"REMOVE-AUTH-{index}",
                    student_number=f"REMOVE-AUTH-{index}",
                    role=User.Role.STUDENT,
                )
                enrollment = CourseEnrollment.objects.create(
                    course=self.course,
                    class_group=self.class_group,
                    student=student,
                )
                self.client.force_login(actor)
                response = self.client.post(
                    self.removal_url(student=student),
                    {"confirmation": "delete"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertFalse(CourseEnrollment.objects.filter(pk=enrollment.pk).exists())

        self.client.force_login(self.outsider)
        denied = self.client.post(
            self.removal_url(),
            {"confirmation": "delete"},
        )
        self.assertEqual(denied.status_code, 404)
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())

    def test_cross_class_student_and_archived_course_are_rejected(self):
        other_class = ClassGroup.objects.create(
            course=self.course,
            name="错误作用域班级",
            academic_year="2025",
        )
        self.client.force_login(self.owner)

        cross_class = self.client.post(
            self.removal_url(class_group=other_class),
            {"confirmation": "delete"},
        )
        self.assertEqual(cross_class.status_code, 404)

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        archived = self.client.post(
            self.removal_url(),
            {"confirmation": "delete"},
        )
        self.assertEqual(archived.status_code, 403)
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())

    def test_inactive_class_allows_removal(self):
        self.class_group.is_active = False
        self.class_group.save(update_fields=["is_active"])
        self.client.force_login(self.owner)

        response = self.client.post(
            self.removal_url(),
            {"confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())

    def test_removal_rolls_back_when_audit_write_fails(self):
        draft = Experiment.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            question_bank=self.bank,
            name="回滚草稿",
        )
        draft_student = ExperimentStudent.objects.create(
            experiment=draft,
            student=self.student,
        )
        self.client.force_login(self.owner)

        with patch(
            "apps.accounts.course_resource_views.log_action",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesMessage(RuntimeError, "audit unavailable"):
                self.client.post(
                    self.removal_url(),
                    {"confirmation": "delete"},
                )

        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertTrue(ExperimentStudent.objects.filter(pk=draft_student.pk).exists())

    def test_concurrent_transfer_rolls_back_the_removal(self):
        other_class = ClassGroup.objects.create(
            course=self.course,
            name="并发转班目标",
            academic_year="2025",
        )
        original_lock = CourseEnrollment.objects.select_for_update

        def move_before_lock(*args, **kwargs):
            CourseEnrollment.objects.filter(pk=self.enrollment.pk).update(
                class_group=other_class
            )
            return original_lock(*args, **kwargs)

        self.client.force_login(self.owner)
        with patch.object(
            CourseEnrollment.objects,
            "select_for_update",
            side_effect=move_before_lock,
        ):
            response = self.client.post(
                self.removal_url(),
                {"confirmation": "delete"},
            )

        self.assertEqual(response.status_code, 302)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.class_group_id, self.class_group.pk)
