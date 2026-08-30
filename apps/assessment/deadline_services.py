"""Single authority for task/session/item deadline decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone


def min_non_null(*values):
    defined = [value for value in values if value is not None]
    return min(defined) if defined else None


@dataclass(frozen=True)
class DeadlineDecision:
    allowed: bool
    code: str = ""
    deadline: object | None = None


class DeadlineDecisionService:
    """Centralized authorization for every assessment time-sensitive write."""

    @classmethod
    def get_task_state(cls, task, at=None):
        at = at or timezone.now()
        if task.cancelled_at is not None:
            return "cancelled"
        if task.manual_closed_at is not None:
            return "manual_closed"
        if task.invalidated_at is not None:
            return "invalidated"
        prefetched = getattr(task, "latest_schedule_revisions", None)
        schedule = (
            next(iter(prefetched), None)
            if prefetched is not None
            else task.schedule_revisions.order_by("-revision_no").first()
        )
        if schedule is None:
            return "open"
        if schedule.opens_at is not None and at < schedule.opens_at:
            return "scheduled"
        if schedule.closes_at is not None and at >= schedule.closes_at:
            return "closed"
        return "open"

    @classmethod
    def get_session_deadline(cls, session):
        return min_non_null(
            getattr(session, "initial_effective_deadline_at", None),
            getattr(session, "authoritative_terminal_at", None),
        )

    @classmethod
    def get_item_deadline(cls, attempt):
        return min_non_null(
            getattr(attempt, "initial_effective_deadline_at", None),
            getattr(attempt, "authoritative_terminal_at", None),
        )

    @classmethod
    def initial_session_deadline(cls, task_close, total_limit_deadline):
        return min_non_null(task_close, total_limit_deadline)

    @classmethod
    def initial_item_deadline(cls, task_close, total_limit_deadline, item_deadline):
        return min_non_null(task_close, total_limit_deadline, item_deadline)

    @classmethod
    def _terminal_decision(cls, task, session=None):
        if task.cancelled_at is not None:
            return DeadlineDecision(False, "TASK_TERMINATED")
        if task.manual_closed_at is not None:
            return DeadlineDecision(False, "TASK_TERMINATED")
        if task.invalidated_at is not None:
            return DeadlineDecision(False, "TASK_TERMINATED")
        if session is not None and getattr(session, "status", "") == "invalidated":
            return DeadlineDecision(False, "TASK_TERMINATED")
        return None

    @classmethod
    def can_start_session(cls, task, request_received_at):
        terminal = cls._terminal_decision(task)
        if terminal is not None:
            return terminal
        state = cls.get_task_state(task, request_received_at)
        if state != "open":
            return DeadlineDecision(False, "TASK_NOT_OPEN")
        return DeadlineDecision(True)

    @classmethod
    def can_enter_item(cls, task, session, request_received_at):
        return cls._can_write(task, session, None, request_received_at)

    @classmethod
    def can_write_response(cls, task, session, attempt, request_received_at):
        return cls._can_write(task, session, attempt, request_received_at)

    @classmethod
    def can_submit(cls, task, session, attempt, request_received_at):
        return cls._can_write(task, session, attempt, request_received_at)

    @classmethod
    def _can_write(cls, task, session, attempt, request_received_at):
        # Dynamic terminal facts are judged after the Task lock is acquired.
        terminal = cls._terminal_decision(task, session)
        if terminal is not None:
            return terminal
        deadline = min_non_null(
            getattr(session, "initial_effective_deadline_at", None),
            getattr(attempt, "initial_effective_deadline_at", None)
            if attempt is not None
            else None,
        )
        if deadline is not None and request_received_at >= deadline:
            return DeadlineDecision(False, "DEADLINE_EXPIRED", deadline)
        return DeadlineDecision(True, deadline=deadline)

    @classmethod
    def item_deadline_from_start(cls, started_at, seconds):
        if started_at is None or not seconds:
            return None
        return started_at + timedelta(seconds=int(seconds))
