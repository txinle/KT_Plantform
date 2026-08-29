from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from apps.assessment.models import AssessmentEvent

from ..teaching_data import TimingKind, summarize_attempt_timing


def _duration_display(value_ms: Decimal | None) -> str:
    if value_ms is None:
        return "—"
    seconds = (value_ms / Decimal("1000")).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{seconds}s"


@dataclass(frozen=True)
class TimingQualityComposition:
    generated_at: datetime
    submitted_count: int
    exact_count: int
    estimated_count: int
    wall_clock_count: int
    unavailable_count: int


@dataclass(frozen=True)
class QuestionTimingSummary:
    generated_at: datetime
    median_ms: Decimal | None
    q1_ms: Decimal | None
    q3_ms: Decimal | None
    sample_size: int
    quality: TimingQualityComposition

    @property
    def median_display(self) -> str:
        return _duration_display(self.median_ms)

    @property
    def iqr_display(self) -> str:
        if self.q1_ms is None or self.q3_ms is None:
            return "—"
        return f"{_duration_display(self.q1_ms)}–{_duration_display(self.q3_ms)}"


def _percentile(values: tuple[Decimal, ...], proportion: Decimal):
    if not values:
        return None
    position = Decimal(len(values) - 1) * proportion
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    fraction = position - Decimal(lower_index)
    return values[lower_index] + (
        values[upper_index] - values[lower_index]
    ) * fraction


def summarize_question_timing(
    *,
    generated_at,
    attempts,
    submissions_by_attempt,
    events_by_attempt=None,
):
    """Summarize observable active time without promoting wall-clock fallback.

    Exact monotonic and synchronized-estimate observations enter the duration
    distribution. Wall-clock fallback and unavailable observations remain in
    the disclosed quality composition only.
    """
    submitted_attempts = tuple(
        attempt for attempt in attempts if attempt.pk in submissions_by_attempt
    )
    if events_by_attempt is None:
        grouped_events = defaultdict(list)
        for event in AssessmentEvent.objects.filter(
            attempt_id__in=[attempt.pk for attempt in submitted_attempts],
            event_type="activity_heartbeat",
        ).order_by("attempt_id", "server_ingest_sequence_no"):
            grouped_events[event.attempt_id].append(event)
        events_by_attempt = grouped_events

    counts = {
        TimingKind.ACTIVE: 0,
        TimingKind.ESTIMATED_ACTIVE: 0,
        TimingKind.WALL_CLOCK: 0,
        TimingKind.UNAVAILABLE: 0,
    }
    durations = []
    for attempt in submitted_attempts:
        timing = summarize_attempt_timing(
            attempt,
            events=events_by_attempt.get(attempt.pk, ()),
            submission=submissions_by_attempt[attempt.pk],
        )
        counts[timing.display_kind] += 1
        if timing.display_kind in {TimingKind.ACTIVE, TimingKind.ESTIMATED_ACTIVE}:
            durations.append(Decimal(timing.display_duration_ms))

    values = tuple(sorted(durations))
    quality = TimingQualityComposition(
        generated_at=generated_at,
        submitted_count=len(submitted_attempts),
        exact_count=counts[TimingKind.ACTIVE],
        estimated_count=counts[TimingKind.ESTIMATED_ACTIVE],
        wall_clock_count=counts[TimingKind.WALL_CLOCK],
        unavailable_count=counts[TimingKind.UNAVAILABLE],
    )
    return QuestionTimingSummary(
        generated_at=generated_at,
        median_ms=_percentile(values, Decimal("0.5")),
        q1_ms=_percentile(values, Decimal("0.25")),
        q3_ms=_percentile(values, Decimal("0.75")),
        sample_size=len(values),
        quality=quality,
    )
