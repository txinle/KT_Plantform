from apps.api.test_support import domain_payload
import os
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.event_services import append_assessment_event
from apps.assessment.models import Attempt, ExperimentSession, GradeRecord, SubmissionEvent, StudentQuestionAssignment
from apps.experiments.models import Experiment, ExperimentClassSnapshot, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank


class TeacherTeachingLegacyApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teaching-api-teacher",
            role=User.Role.TEACHER,
            display_name="教学教师",
        )
        self.outsider = User.objects.create_user(
            username="teaching-api-outsider",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="teaching-api-student",
            role=User.Role.STUDENT,
            student_number="TEACH-001",
            display_name="张同学",
        )
        self.unobserved_student = User.objects.create_user(
            username="teaching-api-unobserved",
            role=User.Role.STUDENT,
            student_number="TEACH-002",
            display_name="李同学",
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="教学数据课程",
            code="TEACH-API",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="教学班",
            academic_year="2026",
        )
        for student in (self.student, self.unobserved_student):
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="教学题库",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="教学实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.class_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="教学题",
            correct_answer="正确",
        )
        snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=question,
            source_sequence=1,
            code="TEACH-Q1",
            question_type=Question.Type.SHORT,
            text="教学题",
            correct_answer="正确",
        )
        self.session = ExperimentSession.objects.create(
            version=self.version,
            student=self.student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=self.session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(
            session=self.session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            first_opened_at=timezone.now(),
            submitted_at=timezone.now(),
        )
        append_assessment_event(
            event_type="item_rendered",
            event_version=1,
            event_domain="assessment",
            actor_source="client",
            session=self.session,
            attempt=self.attempt,
            client_instance_id=uuid.uuid4(),
            client_sequence_no=1,
            client_monotonic_ms=100,
            client_time_origin=timezone.now(),
            payload={},
        )
        submission = SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=uuid.uuid4(),
            final_answer="正确",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=True,
            score=Decimal("1"),
            max_score=Decimal("1"),
        )
        self.client.force_login(self.teacher)

    @property
    def api_url(self):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/teaching/"

    def teaching_scope_query(self):
        return {
            "experiment_id": self.experiment.pk,
            "class_id": self.class_group.pk,
        }

    def test_projects_legacy_roster_filters_sessions_and_attempt_timeline(self):
        response = self.client.get(
            self.api_url,
            {
                **self.teaching_scope_query(),
                "q": "张",
                "student_id": self.student.pk,
                "session_id": self.session.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["scope"]["values"], self.teaching_scope_query())
        self.assertTrue(payload["scope"]["is_complete"])
        self.assertEqual(payload["filters"], {
            "q": "张",
            "student_id": self.student.pk,
            "session_id": self.session.pk,
        })
        self.assertEqual(payload["total_roster_count"], 2)
        self.assertEqual(len(payload["student_summaries"]), 1)
        student_row = payload["student_summaries"][0]
        self.assertEqual(student_row["student_number"], "TEACH-001")
        self.assertEqual(student_row["display_name"], "张同学")
        self.assertEqual(student_row["submitted_count"], 1)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["id"], self.session.pk)
        self.assertEqual(len(payload["attempts"]), 1)
        attempt_row = payload["attempts"][0]
        self.assertEqual(attempt_row["question_code"], "TEACH-Q1")
        self.assertEqual(attempt_row["grade_score"], "1.00")
        self.assertEqual(attempt_row["timeline"][0]["event_type"], "item_rendered")
        self.assertNotIn("payload", attempt_row["timeline"][0])
        self.assertIn("table=assessment_events", attempt_row["raw_events_url"])

    def test_returns_blocked_payload_without_a_complete_teaching_scope(self):
        response = self.client.get(self.api_url)

        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertFalse(payload["scope"]["is_complete"])
        self.assertEqual(payload["blocked_message"], "请先选择实验和班级")
        self.assertEqual(payload["student_summaries"], [])
        self.assertEqual(payload["sessions"], [])
        self.assertEqual(payload["attempts"], [])

    def test_accepts_single_experiment_from_shared_scope(self):
        response = self.client.get(self.api_url, {
            "experiment_ids": str(self.experiment.pk),
            "class_id": self.class_group.pk,
            "student_id": self.student.pk,
            "session_id": self.session.pk,
        })
        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["scope"]["values"], self.teaching_scope_query())
        self.assertTrue(payload["scope"]["is_complete"])
        self.assertEqual(payload["total_roster_count"], 2)
        self.assertEqual(payload["attempts"][0]["grade_score"], "1.00")

    def test_multiple_experiments_prompt_selection_without_selecting_one(self):
        second = Experiment.objects.create(
            course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="另一个教学实验",
        )
        response = self.client.get(self.api_url, {
            "experiment_ids": f"{self.experiment.pk},{second.pk}",
            "class_id": self.class_group.pk,
        })
        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertFalse(payload["scope"]["is_complete"])
        self.assertEqual(payload["blocked_message"], "教学数据一次只查看一个实验，请在上方选择一个实验和班级")
        self.assertEqual(payload["student_summaries"], [])
        self.assertEqual(payload["sessions"], [])
        self.assertEqual(payload["attempts"], [])

    def test_shared_scope_preserves_validation_and_course_authorization(self):
        for query, status in (
            ({"experiment_ids": "999999"}, 404),
            ({"experiment_ids": "invalid"}, 400),
            ({"experiment_ids": str(self.experiment.pk), "experiment_id": 999999}, 400),
        ):
            with self.subTest(query=query):
                response = self.client.get(self.api_url, {"class_id": self.class_group.pk, **query})
                self.assertEqual(response.status_code, status)

    def test_rejects_an_out_of_scope_student_or_session(self):
        outside_student = User.objects.create_user(
            username="teaching-api-outside-student",
            role=User.Role.STUDENT,
        )

        student_response = self.client.get(
            self.api_url,
            {**self.teaching_scope_query(), "student_id": outside_student.pk},
        )
        session_response = self.client.get(
            self.api_url,
            {**self.teaching_scope_query(), "session_id": self.session.pk},
        )

        self.assertEqual(student_response.status_code, 404)
        self.assertEqual(session_response.status_code, 404)

    def test_hides_a_course_the_teacher_cannot_manage(self):
        self.client.force_login(self.outsider)

        response = self.client.get(self.api_url, self.teaching_scope_query())

        self.assertEqual(response.status_code, 404)

    def test_requires_an_authenticated_teacher(self):
        self.client.logout()

        response = self.client.get(self.api_url, self.teaching_scope_query())

        self.assertEqual(response.status_code, 401)
