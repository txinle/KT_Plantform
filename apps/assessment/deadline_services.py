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
    def schedule_at(cls, task, at):
        if not hasattr(task, "schedule_revisions"):
            return None
        prefetched = getattr(task, "latest_schedule_revisions", None)
        if prefetched is not None:
            return next((revision for revision in prefetched if revision.created_at <= at), None)
        return task.schedule_revisions.filter(created_at__lte=at).order_by("-revision_no").first()

    @classmethod
    def manual_close_at(cls, task, at):
        if getattr(task, "manual_closed_at", None) is None:
            return None
        if not hasattr(task, "manual_closures"):
            return task.manual_closed_at if at >= task.manual_closed_at else None
        closure = task.manual_closures.filter(closed_at__lte=at).order_by("-closed_at").first()
        closed_at = closure.closed_at if closure is not None else None
        if task.manual_closed_at and task.manual_closed_at <= at:
            closed_at = max(closed_at or task.manual_closed_at, task.manual_closed_at)
        return closed_at

    @classmethod
    def get_task_state(cls, task, at=None):
        at = at or timezone.now()
        if task.cancelled_at is not None and at >= task.cancelled_at:
            return "cancelled"
        if task.invalidated_at is not None and at >= task.invalidated_at:
            return "invalidated"
        schedule = cls.schedule_at(task, at)
        closed_at = cls.manual_close_at(task, at)
        if closed_at is not None:
            closure = (
                task.manual_closures.filter(closed_at=closed_at).order_by("-pk").first()
                if hasattr(task, "manual_closures") else None
            )
            if closure is not None:
                if schedule is None or schedule.revision_no <= closure.schedule_revision_no or not schedule.reopens_manual_close:
                    return "manual_closed"
            elif schedule is None or schedule.created_at <= closed_at or not schedule.reopens_manual_close:
                return "manual_closed"
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
            getattr(session, "deadline_at", getattr(session, "initial_effective_deadline_at", None)),
            getattr(session, "authoritative_terminal_at", None),
        )

    @classmethod
    def get_item_deadline(cls, attempt):
        return min_non_null(
            getattr(attempt, "current_effective_deadline_at", None)
            or getattr(attempt, "initial_effective_deadline_at", None),
            getattr(attempt, "authoritative_terminal_at", None),
        )

    @classmethod
    def initial_session_deadline(cls, task_close, total_limit_deadline):
        return min_non_null(task_close, total_limit_deadline)

    @classmethod
    def initial_item_deadline(cls, task_close, total_limit_deadline, item_deadline):
        return min_non_null(task_close, total_limit_deadline, item_deadline)

    @classmethod
    def _terminal_decision(cls, task, session=None, request_received_at=None):
        at = request_received_at or timezone.now()
        if task.cancelled_at is not None and at >= task.cancelled_at:
            return DeadlineDecision(False, "TASK_TERMINATED")
        if cls.get_task_state(task, at=at) == "manual_closed":
            return DeadlineDecision(False, "TASK_TERMINATED")
        if task.invalidated_at is not None and at >= task.invalidated_at:
            return DeadlineDecision(False, "TASK_TERMINATED")
        if session is not None and getattr(session, "status", "") == "invalidated":
            terminal_at = getattr(session, "terminal_at", None)
            if terminal_at is None or at >= terminal_at:
                return DeadlineDecision(False, "TASK_TERMINATED")
        return None

    @classmethod
    def can_start_session(cls, task, request_received_at):
        terminal = cls._terminal_decision(task, request_received_at=request_received_at)
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
        # Terminal facts are evaluated against the trusted API arrival time.
        terminal = cls._terminal_decision(
            task,
            session,
            request_received_at=request_received_at,
        )
        if terminal is not None:
            return terminal
        schedule = cls.schedule_at(task, request_received_at)
        state = cls.get_task_state(task, at=request_received_at)
        if state == "closed":
            return DeadlineDecision(False, "DEADLINE_EXPIRED", schedule.closes_at)
        if state != "open":
            return DeadlineDecision(False, "TASK_NOT_OPEN")
        deadline = min_non_null(
            getattr(session, "deadline_at", getattr(session, "initial_effective_deadline_at", None)),
            (getattr(attempt, "current_effective_deadline_at", None)
             or getattr(attempt, "initial_effective_deadline_at", None))
            if attempt is not None
            else None,
            schedule.closes_at if schedule is not None else None,
        )
        if deadline is not None and request_received_at >= deadline:
            return DeadlineDecision(False, "DEADLINE_EXPIRED", deadline)
        return DeadlineDecision(True, deadline=deadline)

    @classmethod
    def item_deadline_from_start(cls, started_at, seconds):
        if started_at is None or not seconds:
            return None
        return started_at + timedelta(seconds=int(seconds))
