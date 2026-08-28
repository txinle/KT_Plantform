"""Presentation-safe teaching adapters built on immutable research records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from apps.assessment.event_invariants import interval_quality
from apps.assessment.models import AssessmentEvent, Attempt, GradeRecord, SubmissionEvent


class TeachingScopeBlocked(ValueError):
    pass


class TimingKind(StrEnum):
    ACTIVE = "active"
    ESTIMATED_ACTIVE = "estimated_active"
    WALL_CLOCK = "wall_clock"
    UNAVAILABLE = "unavailable"


class TeachingDataStatus(StrEnum):
    NOT_PARTICIPATED = "not_participated"
    IN_PROGRESS = "in_progress"
    PENDING_GRADING = "pending_grading"
    COMPLETED = "completed"
    DATA_ERROR = "data_error"


TEACHING_DATA_STATUS_LABELS = {
    TeachingDataStatus.NOT_PARTICIPATED: "未参与",
    TeachingDataStatus.IN_PROGRESS: "进行中",
    TeachingDataStatus.PENDING_GRADING: "待评分",
    TeachingDataStatus.COMPLETED: "已完成",
    TeachingDataStatus.DATA_ERROR: "数据异常",
}


@dataclass(frozen=True)
class TimingSummary:
    active_duration_ms: int | None
    wall_clock_duration_ms: int | None
    display_duration_ms: int | None
    display_kind: TimingKind
    interval_quality: str

    # Transitional presentation aliases.  New callers should use the explicit
    # fields above so an active estimate is never mistaken for total elapsed
    # time.
    @property
    def kind(self):
        return self.display_kind

    @property
    def duration_ms(self):
        return self.display_duration_ms

    @property
    def display_label(self):
        return {
            TimingKind.ACTIVE: "有效作答时间",
            TimingKind.ESTIMATED_ACTIVE: "估算有效作答时间",
            TimingKind.WALL_CLOCK: "总历时",
            TimingKind.UNAVAILABLE: "无法计算",
        }[self.display_kind]


def require_single_version_scope(scope):
    if not scope.is_single_version:
        raise TeachingScopeBlocked("教学数据需要明确选择一个实验版本")
    return scope


def _event_timestamp(event):
    return event.estimated_occurred_at_utc or event.server_received_at


def _observed_active_duration(events):
    heartbeats = [event for event in events if event.event_type == "activity_heartbeat"]
    if len(heartbeats) < 2:
        return None, "unknown"
    total_ms = 0
    quality = "exact_monotonic"
    for previous, current in zip(heartbeats, heartbeats[1:]):
        current_quality = interval_quality(previous, current)
        if current_quality not in {"exact_monotonic", "synchronized_estimate"}:
            # A partial active total would look more precise than the evidence
            # allows.  The UI must instead disclose total elapsed time.
            return None, "degraded"
        if current_quality == "synchronized_estimate":
            quality = "synchronized_estimate"
        if current_quality == "exact_monotonic":
            delta = current.client_monotonic_ms - previous.client_monotonic_ms
            delta_ms = int(delta)
        else:
            delta_ms = int((_event_timestamp(current) - _event_timestamp(previous)).total_seconds() * 1000)
        # Heartbeats describe a bounded, visible/focused observation interval;
        # an arbitrarily long gap is never silently treated as active time.
        if 0 < delta_ms <= 90_000:
            total_ms += delta_ms
    if total_ms == 0:
        return None, quality
    return total_ms, quality


_SUBMISSION_NOT_LOADED = object()


def summarize_attempt_timing(
    attempt, events=None, submission=_SUBMISSION_NOT_LOADED
):
    """Return the strongest defensible duration without reading active_seconds."""
    if events is None:
        events = list(
            AssessmentEvent.objects.filter(attempt=attempt).order_by(
                "server_ingest_sequence_no"
            )
        )
    observed_ms, quality = _observed_active_duration(events)
    if submission is _SUBMISSION_NOT_LOADED and attempt.pk:
        submission = (
            SubmissionEvent.objects.filter(attempt=attempt)
            .only("committed_at")
            .first()
        )
    elif submission is _SUBMISSION_NOT_LOADED:
        submission = None
    wall_clock_ms = None
    if attempt.first_opened_at is not None and submission is not None:
        wall_clock_ms = int(
            (submission.committed_at - attempt.first_opened_at).total_seconds() * 1000
        )
        if wall_clock_ms < 0:
            wall_clock_ms = None
    if observed_ms is not None:
        kind = (
            TimingKind.ACTIVE
            if quality == "exact_monotonic"
            else TimingKind.ESTIMATED_ACTIVE
        )
        return TimingSummary(
            active_duration_ms=observed_ms,
            wall_clock_duration_ms=wall_clock_ms,
            display_duration_ms=observed_ms,
            display_kind=kind,
            interval_quality=quality,
        )
    if wall_clock_ms is not None:
        return TimingSummary(
            active_duration_ms=None,
            wall_clock_duration_ms=wall_clock_ms,
            display_duration_ms=wall_clock_ms,
            display_kind=TimingKind.WALL_CLOCK,
            interval_quality=quality,
        )
    return TimingSummary(
        active_duration_ms=None,
        wall_clock_duration_ms=None,
        display_duration_ms=None,
        display_kind=TimingKind.UNAVAILABLE,
        interval_quality=quality,
    )


def effective_grade_for_attempt(attempt):
    return (
        GradeRecord.objects.filter(
            submission__attempt=attempt,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        )
        .order_by("-version")
        .first()
    )


def _teaching_data_status(sessions, submitted_count, graded_count):
    if not sessions:
        return TeachingDataStatus.NOT_PARTICIPATED
    if any(session.status == session.Status.INVALIDATED for session in sessions):
        return TeachingDataStatus.DATA_ERROR
    if any(session.status == session.Status.IN_PROGRESS for session in sessions):
        return TeachingDataStatus.IN_PROGRESS
    if submitted_count > graded_count:
        return TeachingDataStatus.PENDING_GRADING
    if any(session.status == session.Status.COMPLETED for session in sessions):
        return TeachingDataStatus.COMPLETED
    return TeachingDataStatus.DATA_ERROR


def build_teaching_roster(scope):
    """Build teaching rows from the class roster, never from observed sessions."""
    enrollments = list(scope.roster())
    rows = {
        enrollment.student_id: {
            "student": enrollment.student,
            "sessions": [],
            "attempts": [],
            "effective_grades": [],
        }
        for enrollment in enrollments
    }
    sessions = list(scope.sessions().select_related("student", "version"))
    attempts = list(
        scope.attempts().select_related(
            "session__student",
            "assignment__snapshot",
            "assignment__item_instance",
        )
    )
    effective_grades = {
        grade.submission.attempt_id: grade
        for grade in GradeRecord.objects.filter(
            submission__attempt__in=attempts,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).select_related("submission")
    }
    for session in sessions:
        if session.student_id in rows:
            rows[session.student_id]["sessions"].append(session)
    for attempt in attempts:
        student_id = attempt.session.student_id
        if student_id not in rows:
            continue
        rows[student_id]["attempts"].append(attempt)
        if attempt.pk in effective_grades:
            rows[student_id]["effective_grades"].append(effective_grades[attempt.pk])

    summaries = []
    for row in rows.values():
        observed = bool(row["sessions"])
        evaluated = row["effective_grades"]
        submitted_count = sum(
            attempt.status == Attempt.Status.SUBMITTED for attempt in row["attempts"]
        )
        graded_count = len(evaluated)
        scores = [grade.score for grade in evaluated if grade.score is not None]
        max_scores = [
            grade.max_score for grade in evaluated if grade.max_score is not None
        ]
        status = _teaching_data_status(
            row["sessions"], submitted_count, graded_count
        )
        correct_count = sum(grade.is_correct is True for grade in evaluated)
        summaries.append(
            {
                "student": row["student"],
                "sessions": row["sessions"],
                "session_count": len(row["sessions"]) if observed else None,
                "attempt_count": len(row["attempts"]) if observed else None,
                "submitted_count": submitted_count if observed else None,
                "graded_count": graded_count if observed else None,
                "correct_count": correct_count if observed else None,
                "correct_rate": (
                    round(correct_count * 100 / graded_count)
                    if graded_count
                    else None
                ),
                "score": sum(scores) if scores else None,
                "max_score": sum(max_scores) if max_scores else None,
                "data_status": status,
                "data_status_label": TEACHING_DATA_STATUS_LABELS[status],
            }
        )
    return summaries


def attempt_timeline(attempt):
    """Expose source event labels and timestamps without psychological labels."""
    return [
        {
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "occurred_at": _event_timestamp(event),
            "interval_quality": event.interval_quality,
            "payload": event.payload,
        }
        for event in AssessmentEvent.objects.filter(attempt=attempt).order_by(
            "server_ingest_sequence_no"
        )
    ]
