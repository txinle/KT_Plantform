from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.assessment.deadline_services import DeadlineDecisionService, min_non_null


class DeadlineCalculationTests(SimpleTestCase):
    def test_min_non_null_uses_the_earliest_defined_deadline(self):
        base = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)

        self.assertEqual(
            min_non_null(None, base + timedelta(minutes=45), base + timedelta(minutes=20)),
            base + timedelta(minutes=20),
        )

    def test_request_at_a_deterministic_deadline_is_rejected(self):
        deadline = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        task = SimpleNamespace(
            cancelled_at=None,
            manual_closed_at=None,
            invalidated_at=None,
        )
        session = SimpleNamespace(
            initial_effective_deadline_at=deadline,
            status="in_progress",
        )

        before = DeadlineDecisionService.can_enter_item(
            task,
            session,
            deadline - timedelta(microseconds=1),
        )
        at_boundary = DeadlineDecisionService.can_enter_item(task, session, deadline)

        self.assertTrue(before.allowed)
        self.assertFalse(at_boundary.allowed)
        self.assertEqual(at_boundary.code, "DEADLINE_EXPIRED")

    def test_unopened_attempt_is_still_bounded_by_the_session_deadline(self):
        deadline = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        task = SimpleNamespace(
            cancelled_at=None,
            manual_closed_at=None,
            invalidated_at=None,
        )
        session = SimpleNamespace(
            initial_effective_deadline_at=deadline,
            status="in_progress",
        )
        attempt = SimpleNamespace(initial_effective_deadline_at=None)

        decision = DeadlineDecisionService.can_submit(
            task,
            session,
            attempt,
            deadline,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "DEADLINE_EXPIRED")
        self.assertEqual(decision.deadline, deadline)

    def test_committed_manual_close_overrides_an_earlier_request_time(self):
        request_received_at = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
        task = SimpleNamespace(
            cancelled_at=None,
            manual_closed_at=request_received_at + timedelta(seconds=1),
            invalidated_at=None,
        )
        session = SimpleNamespace(
            initial_effective_deadline_at=request_received_at + timedelta(minutes=10),
            status="in_progress",
        )

        decision = DeadlineDecisionService.can_submit(
            task,
            session,
            None,
            request_received_at,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "TASK_TERMINATED")
