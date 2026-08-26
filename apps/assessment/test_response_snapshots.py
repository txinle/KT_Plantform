import hashlib
import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .models import (
    Attempt,
    DraftSaveEvent,
    ExperimentSession,
    ResponseSnapshot,
    StudentQuestionAssignment,
)
from .services import save_draft


class ResponseSnapshotServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="response-snapshot-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="RESPONSE-SNAPSHOT-S1",
            student_number="RESPONSE-SNAPSHOT-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="响应快照题库")
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="响应快照实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.session = ExperimentSession.objects.create(
            version=self.version,
            student=self.student,
        )
        self.attempts = {}
        question_types = (
            Question.Type.SHORT,
            Question.Type.FILL_BLANK,
            Question.Type.NUMERIC,
            Question.Type.SINGLE,
            Question.Type.MULTIPLE,
            Question.Type.TRUE_FALSE,
        )
        for sequence, question_type in enumerate(question_types, start=1):
            question = Question.objects.create(
                bank=bank,
                sequence=sequence,
                question_type=question_type,
                text=f"{question_type} 题目",
                correct_answer=(
                    ["A", "B"]
                    if question_type == Question.Type.MULTIPLE
                    else False
                    if question_type == Question.Type.TRUE_FALSE
                    else "A"
                ),
            )
            options = []
            if question_type in {Question.Type.SINGLE, Question.Type.MULTIPLE}:
                options = [
                    {"key": "A", "text": "甲"},
                    {"key": "B", "text": "乙"},
                ]
            snapshot = QuestionSnapshot.objects.create(
                version=self.version,
                source_question=question,
                source_sequence=sequence,
                code=f"Q{sequence}",
                question_type=question_type,
                text=f"{question_type} 题目",
                options=options,
                correct_answer=question.correct_answer,
            )
            assignment = StudentQuestionAssignment.objects.create(
                session=self.session,
                snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot),
                assigned_sequence_index=sequence,
            )
            self.attempts[question_type] = Attempt.objects.create(
                session=self.session,
                assignment=assignment,
            )
        self.client.force_login(self.student)

    def post_draft(self, question_type, answer):
        attempt = self.attempts[question_type]
        return self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": answer}),
            content_type="application/json",
        )

    def test_snapshot_uses_utf8_hash_exact_character_length_and_attempt_scope(self):
        from .response_snapshot_services import create_response_snapshot

        content = "电路稳定性：αβ🙂"
        first = create_response_snapshot(
            attempt=self.attempts[Question.Type.SHORT],
            content=content,
        )
        replay = create_response_snapshot(
            attempt=self.attempts[Question.Type.SHORT],
            content=content,
        )
        other_attempt = create_response_snapshot(
            attempt=self.attempts[Question.Type.FILL_BLANK],
            content=content,
        )

        self.assertEqual(first.pk, replay.pk)
        self.assertNotEqual(first.pk, other_attempt.pk)
        self.assertEqual(first.content_length, len(content))
        self.assertEqual(
            first.content_hash,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def test_text_like_drafts_create_and_reuse_exact_snapshots_including_empty(self):
        cases = (
            (Question.Type.SHORT, "第一版简答"),
            (Question.Type.FILL_BLANK, ""),
            (Question.Type.NUMERIC, "1.2500"),
        )
        for question_type, answer in cases:
            with self.subTest(question_type=question_type):
                attempt = save_draft(self.attempts[question_type].pk, answer)
                snapshot = attempt.saved_response_snapshot
                self.assertEqual(snapshot.content, answer)
                self.assertEqual(snapshot.content_length, len(answer))
                save_draft(self.attempts[question_type].pk, answer)
                self.assertEqual(
                    ResponseSnapshot.objects.filter(attempt=attempt).count(),
                    1,
                )

    def test_choice_drafts_do_not_create_response_snapshots(self):
        for question_type, answer in (
            (Question.Type.SINGLE, "A"),
            (Question.Type.MULTIPLE, ["B", "A", "B"]),
            (Question.Type.TRUE_FALSE, "false"),
        ):
            with self.subTest(question_type=question_type):
                attempt = save_draft(self.attempts[question_type].pk, answer)
                self.assertIsNone(attempt.saved_response_snapshot)
                self.assertFalse(
                    ResponseSnapshot.objects.filter(attempt=attempt).exists()
                )

    def test_snapshot_failure_rolls_back_business_draft_and_legacy_event(self):
        attempt = self.attempts[Question.Type.SHORT]

        with patch(
            "apps.assessment.services.create_response_snapshot",
            side_effect=RuntimeError("snapshot unavailable"),
        ), self.assertRaises(RuntimeError):
            save_draft(attempt.pk, "不能部分保存")

        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)
        self.assertEqual(DraftSaveEvent.objects.filter(attempt=attempt).count(), 0)
        self.assertEqual(ResponseSnapshot.objects.filter(attempt=attempt).count(), 0)

    def test_draft_endpoint_returns_snapshot_reference_without_raw_text(self):
        answer = "包含 Unicode 的简答🙂"
        response = self.post_draft(Question.Type.SHORT, answer)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        snapshot = ResponseSnapshot.objects.get(
            attempt=self.attempts[Question.Type.SHORT]
        )
        self.assertEqual(data["response_snapshot_id"], str(snapshot.pk))
        self.assertEqual(data["content_hash"], snapshot.content_hash)
        self.assertEqual(data["content_length"], len(answer))
        self.assertNotIn(answer, response.content.decode("utf-8"))

    def test_draft_endpoint_returns_canonical_choice_state(self):
        cases = (
            (Question.Type.SINGLE, "A", "A"),
            (Question.Type.MULTIPLE, ["B", "A", "B"], ["A", "B"]),
            (Question.Type.TRUE_FALSE, False, "false"),
        )
        for question_type, answer, expected in cases:
            with self.subTest(question_type=question_type):
                response = self.post_draft(question_type, answer)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["response_state_after"],
                    expected,
                )
