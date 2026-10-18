from apps.api.test_support import domain_payload
import json
from uuid import uuid4
from django.test import Client, TestCase
from unittest.mock import patch
from django.urls import reverse

from apps.accounts.models import Course, CourseTeacher, User
from apps.analytics.models import ResearchSessionMaterialization
from apps.assessment.models import ExperimentSession, StudentQuestionAssignment
from apps.audit.models import AuditLog

from .models import Experiment, ExperimentVersion, QuestionSnapshot
from .presentation_instances import get_or_create_default_item_instance


class ExperimentDeletionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="delete-owner", password="password123", role=User.Role.TEACHER
        )
        self.collaborator = User.objects.create_user(
            username="delete-collab", password="password123", role=User.Role.TEACHER
        )
        self.outsider = User.objects.create_user(
            username="delete-outsider", password="password123", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            name="删除测试课程", code="DEL101", term="2026春", owner=self.owner
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        from apps.question_bank.models import QuestionBank

        self.bank = QuestionBank.objects.create(
            course=self.course, owner=self.owner, created_by=self.owner, name="删除题库"
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            owner=self.owner,
            created_by=self.owner,
            question_bank=self.bank,
            name="待删除实验",
            status=Experiment.Status.PUBLISHED,
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.owner,
            configuration={},
        )

    def _confirmed_delete(self, url):
        token = domain_payload(self.client.get(url))["confirmation_token"]
        operation_id = str(uuid4())
        registered = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/deletion-operations/",
            json.dumps({"operation_id": operation_id, "action": "delete_experiment",
                        "object_id": self.experiment.pk, "class_id": None,
                        "confirmation_token": token}),
            content_type="application/json",
        )
        self.assertEqual(registered.status_code, 201)
        return self.client.post(url, {"operation_id": operation_id,
                                     "confirmation": "delete", "confirmation_token": token})

    def test_delete_requires_explicit_second_confirmation(self):
        self.client.force_login(self.owner)
        url = reverse("teacher-experiment-deletion", args=[self.course.pk, self.experiment.pk])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_collaborator_can_delete_after_confirmation_and_version_tree_is_removed(self):
        self.client.force_login(self.collaborator)
        from apps.question_bank.models import Question

        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="删除测试题",
            correct_answer="答案",
        )
        snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=question,
            source_sequence=1,
            code=question.code,
            question_type=question.question_type,
            text=question.text,
            correct_answer=question.correct_answer,
            primary_kc="删除测试 KC",
        )
        session = ExperimentSession.objects.create(
            version=self.version,
            student=self.owner,
            class_snapshot_name="教师",
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        url = reverse("teacher-experiment-deletion", args=[self.course.pk, self.experiment.pk])

        response = self._confirmed_delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertFalse(ExperimentVersion.objects.filter(pk=self.version.pk).exists())
        self.assertFalse(ExperimentSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(QuestionSnapshot.objects.filter(pk=snapshot.pk).exists())
        self.assertFalse(
            StudentQuestionAssignment.objects.filter(pk=assignment.pk).exists()
        )
        audit = AuditLog.objects.get(action="experiment.delete")
        self.assertEqual(audit.details["experiment_id"], self.experiment.pk)

    def test_delete_experiment_cascades_materialized_session_results(self):
        from .services import delete_experiment

        session = ExperimentSession.objects.create(
            version=self.version,
            student=self.owner,
            class_snapshot_name="教师",
        )
        materialization = ResearchSessionMaterialization.objects.create(session=session)

        delete_experiment(self.experiment)

        self.assertFalse(
            ResearchSessionMaterialization.objects.filter(pk=materialization.pk).exists()
        )

    def test_outsider_cannot_delete_experiment(self):
        self.client.force_login(self.outsider)
        url = reverse("teacher-experiment-deletion", args=[self.course.pk, self.experiment.pk])

        self.assertEqual(self.client.post(url, {"confirmation": "delete", "confirmation_token": "unconfirmed"}).status_code, 404)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_published_delete_is_csrf_protected(self):
        self.client.force_login(self.owner)
        delete_url = reverse(
            "teacher-experiment-deletion", args=[self.course.pk, self.experiment.pk]
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(
            csrf_client.post(delete_url, {"confirmation": "delete", "confirmation_token": "unconfirmed"}).status_code,
            403,
        )

    def test_delete_rolls_back_when_audit_write_fails(self):
        self.client.force_login(self.owner)
        url = reverse("teacher-experiment-deletion", args=[self.course.pk, self.experiment.pk])

        with patch("apps.experiments.research_deletion.log_action", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self._confirmed_delete(url)

        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertTrue(ExperimentVersion.objects.filter(pk=self.version.pk).exists())
