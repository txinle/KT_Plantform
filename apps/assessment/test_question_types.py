import json
import uuid
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .services import get_or_create_session


class StudentQuestionTypeFlowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="student-type-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="student-type-student",
            student_number="student-type-student",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="学生题型题库")
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="题型 KC",
            normalized_name="题型 kc",
        )
        fill_blank = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.FILL_BLANK,
            text="KT 的全称是？",
            correct_answer="Knowledge Tracing",
            accepted_answers=["KT"],
        )
        judgement = Question.objects.create(
            bank=bank,
            sequence=2,
            question_type=Question.Type.TRUE_FALSE,
            text="所有学生会收到相同的配额题集。",
            correct_answer=False,
        )
        for question in (fill_blank, judgement):
            QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="学生题型实验",
            feedback_mode=Experiment.Feedback.FULL,
        )
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()
        self.session = get_or_create_session(self.experiment, self.student)
        self.client.force_login(self.student)

    def assignment_for(self, question_type):
        return self.session.assignments.get(snapshot__question_type=question_type)

    def save_and_submit(self, assignment, answer):
        page = self.client.get(
            reverse("assessment:question", args=[self.session.pk, assignment.pk])
        )
        self.assertEqual(page.status_code, 200)
        save_response = self.client.post(
            reverse("assessment:save_draft", args=[assignment.attempt.pk]),
            data=json.dumps({"answer": answer}),
            content_type="application/json",
        )
        self.assertEqual(save_response.status_code, 200)
        return self.client.post(
            reverse("assessment:submit", args=[assignment.attempt.pk]),
            data=json.dumps({"submission_token": str(uuid.uuid4())}),
            content_type="application/json",
        )

    def test_fill_blank_renders_single_line_restores_draft_and_scores_accepted_answer(self):
        assignment = self.assignment_for(Question.Type.FILL_BLANK)
        response = self.client.get(
            reverse("assessment:question", args=[self.session.pk, assignment.pk])
        )

        self.assertContains(response, "填空答案")
        self.assertContains(response, 'type="text"')
        self.assertNotContains(response, '<textarea id="answer-input"')

        submitted = self.save_and_submit(assignment, "  kt  ")
        self.assertEqual(submitted.status_code, 200)
        self.assertTrue(submitted.json()["is_correct"])
        self.assertEqual(submitted.json()["correct_answer"], "Knowledge Tracing")

    def test_judgement_renders_fixed_bool_payload_and_is_available_in_identified_export(self):
        assignment = self.assignment_for(Question.Type.TRUE_FALSE)
        page = self.client.get(
            reverse("assessment:question", args=[self.session.pk, assignment.pk])
        )
        self.assertContains(page, 'value="true"')
        self.assertContains(page, 'value="false"')

        submitted = self.save_and_submit(assignment, "false")
        self.assertEqual(submitted.status_code, 200)
        self.assertTrue(submitted.json()["is_correct"])
        self.assertIs(submitted.json()["correct_answer"], False)

        self.client.force_login(self.teacher)
        exported = self.client.post(
            reverse("analytics:identified_export", args=[self.experiment.pk]),
            {"fields": ["question_type", "final_answer", "is_correct"]},
        )
        self.assertEqual(exported.status_code, 200)
        rows = list(load_workbook(BytesIO(exported.content), data_only=True).active.values)
        self.assertIn((Question.Type.TRUE_FALSE, "false", True), rows)
