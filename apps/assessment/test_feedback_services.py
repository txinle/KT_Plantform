import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .feedback_services import (
    FeedbackReleaseError,
    initial_grading_result,
    prepare_feedback_exposure,
)
from .models import SubmissionEvent


class InitialGradingResultTests(SimpleTestCase):
    @patch("apps.assessment.capture_v2_services.uses_capture_v2", return_value=True)
    def test_capture_v2_short_answer_starts_pending_without_zero_values(self, _uses_v2):
        attempt = SimpleNamespace(
            session=SimpleNamespace(),
            assignment=SimpleNamespace(
                snapshot=SimpleNamespace(question_type="short_answer")
            ),
        )

        result = initial_grading_result(attempt, correct=True, score=1)

        self.assertEqual(result.status, SubmissionEvent.GradingStatus.PENDING)
        self.assertIsNone(result.is_correct)
        self.assertIsNone(result.score)
        self.assertIsNone(result.max_score)
        self.assertEqual(result.method, "manual_short_answer_v1")

    @patch("apps.assessment.capture_v2_services.uses_capture_v2", return_value=False)
    def test_legacy_or_non_capture_v2_answer_keeps_automatic_grade(self, _uses_v2):
        attempt = SimpleNamespace(
            session=SimpleNamespace(),
            assignment=SimpleNamespace(
                snapshot=SimpleNamespace(question_type="short_answer")
            ),
        )

        result = initial_grading_result(attempt, correct=True, score=1)

        self.assertEqual(result.status, SubmissionEvent.GradingStatus.COMPLETED)
        self.assertTrue(result.is_correct)
        self.assertEqual(result.score, 1)
        self.assertEqual(result.max_score, 1)
        self.assertEqual(result.method, "automatic")


class FeedbackExposurePreparationTests(SimpleTestCase):
    def setUp(self):
        self.answer_event_id = uuid.uuid4()
        self.snapshot_id = uuid.uuid4()
        self.attempt = SimpleNamespace(
            final_response_event_id=self.answer_event_id,
            final_response_snapshot_id=self.snapshot_id,
        )
        self.session = SimpleNamespace(class_task=object())

    def parsed(self, *, stage, flags):
        return SimpleNamespace(
            attempt=self.attempt,
            session=self.session,
            payload={
                "capture_version": "capture-v2",
                "operation_id": str(uuid.uuid4()),
                "feedback_mode": "full",
                "stage": stage,
                "answer_event_id": str(self.answer_event_id),
                "snapshot_id": str(self.snapshot_id),
                "included_correctness": flags[0],
                "included_correct_answer": flags[1],
                "included_explanation": flags[2],
            },
        )

    @patch("apps.assessment.models.AssessmentEvent.objects.filter")
    @patch("apps.assessment.feedback_services.feedback_availability")
    def test_full_feedback_stages_require_the_actual_order_and_cumulative_leave_flags(
        self,
        availability,
        event_filter,
    ):
        availability.return_value = SimpleNamespace(
            released=True,
            feedback_mode="full",
            allows_correctness=True,
            allows_full=True,
        )
        operation_id = str(uuid.uuid4())

        def parsed(stage, flags):
            value = self.parsed(stage=stage, flags=flags)
            value.payload["operation_id"] = operation_id
            return value

        detail = parsed("detail_entered", (True, False, False))
        event_filter.return_value.values_list.return_value = []
        self.assertEqual(prepare_feedback_exposure(detail), detail.payload)

        answer = parsed("answer_presented", (True, True, False))
        event_filter.return_value.values_list.return_value = [detail.payload]
        self.assertEqual(prepare_feedback_exposure(answer), answer.payload)

        explanation = parsed("explanation_presented", (True, True, True))
        event_filter.return_value.values_list.return_value = [detail.payload, answer.payload]
        self.assertEqual(prepare_feedback_exposure(explanation), explanation.payload)

        left = parsed("detail_left", (True, True, True))
        event_filter.return_value.values_list.return_value = [
            detail.payload,
            answer.payload,
            explanation.payload,
        ]
        self.assertEqual(prepare_feedback_exposure(left), left.payload)

        out_of_order = parsed("explanation_presented", (True, True, True))
        event_filter.return_value.values_list.return_value = [detail.payload]
        with self.assertRaises(FeedbackReleaseError):
            prepare_feedback_exposure(out_of_order)

    @patch("apps.assessment.models.AssessmentEvent.objects.filter")
    @patch("apps.assessment.feedback_services.feedback_availability")
    def test_pending_grade_can_record_detail_entry_but_not_answer_or_explanation(
        self,
        availability,
        event_filter,
    ):
        availability.return_value = SimpleNamespace(
            released=True,
            feedback_mode="full",
            allows_correctness=False,
            allows_full=False,
        )
        event_filter.return_value.values_list.return_value = []
        entered = self.parsed(
            stage="detail_entered",
            flags=(False, False, False),
        )
        self.assertEqual(prepare_feedback_exposure(entered), entered.payload)

        with self.assertRaises(FeedbackReleaseError):
            prepare_feedback_exposure(
                self.parsed(
                    stage="answer_presented",
                    flags=(True, True, False),
                )
            )
