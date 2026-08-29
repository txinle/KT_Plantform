from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum


class DataState(StrEnum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    PARTIAL = "partial"
    CONFLICT = "conflict"


class ExclusionReason(StrEnum):
    NO_ELIGIBLE_PARTICIPANTS = "no_eligible_participants"
    NOT_PARTICIPATED = "not_participated"
    INVALIDATED_SESSION = "invalidated_session"
    NOT_SUBMITTED = "not_submitted"
    PENDING_GRADING = "pending_grading"
    VOIDED_OR_SUPERSEDED_GRADE = "voided_or_superseded_grade"
    UNGRADED_OBJECTIVE_RESULT = "ungraded_objective_result"
    INCOMPLETE_SCORE_PAIR = "incomplete_score_pair"
    CONFLICTING_TASK = "conflicting_task"


@dataclass(frozen=True)
class MetricValue:
    generated_at: datetime
    numerator: Decimal
    denominator: Decimal
    ratio: Decimal | None
    state: DataState
    sample_size: int | None = None

    @property
    def percent_display(self):
        if self.ratio is None:
            return "—"
        percentage = (self.ratio * Decimal("100")).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )
        return f"{percentage}%"

    @property
    def percent_css(self):
        if self.ratio is None:
            return "0"
        bounded = min(max(self.ratio, Decimal("0")), Decimal("1"))
        return str((bounded * Decimal("100")).quantize(Decimal("0.1")))

    @classmethod
    def fraction(
        cls,
        *,
        generated_at: datetime,
        numerator,
        denominator,
        sample_size: int | None = None,
    ):
        numerator = Decimal(numerator)
        denominator = Decimal(denominator)
        return cls(
            generated_at=generated_at,
            numerator=numerator,
            denominator=denominator,
            ratio=(numerator / denominator if denominator else None),
            state=DataState.AVAILABLE if denominator else DataState.NO_DATA,
            sample_size=sample_size,
        )


@dataclass(frozen=True)
class StudentComparison:
    generated_at: datetime
    metric_key: str
    student_value: MetricValue
    class_average: MetricValue
    sample_size: int


@dataclass(frozen=True)
class ClassAnalysisScope:
    generated_at: datetime
    course_id: int
    class_group_id: int
    experiment_id: int | None
    task_count: int


@dataclass(frozen=True)
class ClassOverview:
    generated_at: datetime
    roster_count: int
    participated_count: int
    correct_rate: MetricValue
    completion_rate: MetricValue


@dataclass(frozen=True)
class DistributionBin:
    generated_at: datetime
    key: str
    label: str
    count: int


@dataclass(frozen=True)
class ParticipationBreakdown:
    generated_at: datetime
    completed: int
    in_progress: int
    not_participated: int


@dataclass(frozen=True)
class KCRateRow:
    generated_at: datetime
    name: str
    correct_rate: MetricValue


@dataclass(frozen=True)
class ExperimentSummaryRow:
    generated_at: datetime
    experiment_id: int
    name: str
    is_live: bool
    published_at: datetime
    eligible_count: int
    participated_count: int
    completed_count: int
    correct_rate: MetricValue
    score_rate: MetricValue


@dataclass(frozen=True)
class ExperimentOption:
    generated_at: datetime
    experiment_id: int
    name: str
    published_at: datetime


@dataclass(frozen=True)
class StudentSummaryRow:
    generated_at: datetime
    student_id: int
    student_number: str
    display_name: str
    completed_experiments: int
    eligible_experiments: int
    correct_rate: MetricValue
    score_rate: MetricValue
    average_answer_time_ms: Decimal | None
    status: str
    rank: int | None

    @property
    def average_answer_time_display(self):
        if self.average_answer_time_ms is None:
            return "—"
        seconds = (self.average_answer_time_ms / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{seconds}s"


@dataclass(frozen=True)
class StudentPage:
    generated_at: datetime
    items: tuple[StudentSummaryRow, ...]
    page: int
    page_size: int
    page_count: int
    total_count: int
    previous_page: int | None
    next_page: int | None


@dataclass(frozen=True)
class QuestionSummaryRow:
    generated_at: datetime
    question_snapshot_id: int
    code: str
    text: str
    kcs: tuple[str, ...]
    answer_count: int
    correct_count: int
    incorrect_count: int
    correct_rate: MetricValue
    average_answer_time_ms: Decimal | None

    @property
    def average_answer_time_display(self):
        if self.average_answer_time_ms is None:
            return "—"
        seconds = (self.average_answer_time_ms / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{seconds}s"


@dataclass(frozen=True)
class ClassCenterSnapshot:
    generated_at: datetime
    scope: ClassAnalysisScope
    has_live_experiments: bool
    rank_sample_size: int
    overview: ClassOverview
    class_average_correct_rate: MetricValue
    correct_rate_distribution: tuple[DistributionBin, ...]
    participation: ParticipationBreakdown
    kc_rows: tuple[KCRateRow, ...]
    experiment_options: tuple[ExperimentOption, ...]
    experiment_rows: tuple[ExperimentSummaryRow, ...]
    students: StudentPage
    question_rows: tuple[QuestionSummaryRow, ...]
