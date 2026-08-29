from __future__ import annotations

from collections import Counter
from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum

from apps.accounts.models import CourseEnrollment, User
from apps.assessment.models import (
    AssessmentEvent,
    Attempt,
    ExperimentSession,
    GradeRecord,
    ResponseInputObservation,
    ResponseSnapshot,
    SubmissionEvent,
)
from apps.assessment.response_snapshot_services import canonical_response_state
from apps.experiments.models import ExperimentVersionParticipantSnapshot, QuestionSnapshot
from apps.experiments.presentation import display_text, format_answer, options_for_snapshot
from apps.analytics.models import RecordingCoverageAttempt

from .comparisons import competition_ranks
from .contracts import MetricValue, StudentComparison
from .kc_time import QuestionTimingSummary, summarize_question_timing
from .process_language import (
    coverage_reason_labels,
    coverage_status_message,
    close_reason_label,
    event_time_source_message,
    time_quality_message,
)
from .process import (
    AttemptProcessProjection,
    ProcessCompleteness,
    ProcessStep,
    order_process_records,
    project_attempt_process,
)
from .scope import analytics_read_transaction, resolve_task_facts


class TransitionApplicability(StrEnum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class OptionDistributionRow:
    generated_at: datetime
    key: str
    label: str
    count: int
    is_correct: bool


@dataclass(frozen=True)
class TransitionRow:
    generated_at: datetime
    key: str
    label: str
    count: int


@dataclass(frozen=True)
class QuestionParticipantRow:
    generated_at: datetime
    student_id: int
    student_number: str
    display_name: str
    is_current_member: bool
    result_label: str
    final_answer: str
    process_complete: bool


@dataclass(frozen=True)
class QuestionDetailSnapshot:
    generated_at: datetime
    experiment_id: int
    experiment_name: str
    question_id: int
    code: str
    text: str
    question_type: str
    kcs: tuple[str, ...]
    eligible_count: int
    current_member_count: int
    historical_participant_count: int
    answer_count: int
    correct_count: int
    incorrect_count: int
    correct_rate: MetricValue
    timing: QuestionTimingSummary
    chain_coverage: MetricValue
    option_rows: tuple[OptionDistributionRow, ...]
    transition_applicability: TransitionApplicability
    transition_rows: tuple[TransitionRow, ...]
    participants: tuple[QuestionParticipantRow, ...]

@dataclass(frozen=True)
class AttemptProcessDetail:
    generated_at: datetime
    experiment_id: int
    experiment_name: str
    question_id: int
    question_code: str
    question_text: str
    student_id: int
    student_number: str
    display_name: str
    is_current_member: bool
    result_label: str
    score_display: str
    process: AttemptProcessProjection
    coverage_status_title: str
    coverage_status_description: str
    coverage_time_title: str
    coverage_time_description: str
    coverage_reason_labels: tuple[str, ...]
    has_cross_instance_order_uncertainty: bool


@dataclass(frozen=True)
class StudentExperimentPerformanceRow:
    generated_at: datetime
    experiment_id: int
    name: str
    completed: bool
    correct_rate: MetricValue
    score_rate: MetricValue
    rank: int | None
    rank_sample_size: int
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
class StudentTrendRow:
    generated_at: datetime
    experiment_id: int
    name: str
    student_correct_rate: MetricValue
    class_average_correct_rate: MetricValue
    x: Decimal
    student_y: Decimal
    class_y: Decimal


@dataclass(frozen=True)
class StudentKCComparisonRow:
    generated_at: datetime
    name: str
    comparison: StudentComparison


@dataclass(frozen=True)
class StudentDetailSnapshot:
    generated_at: datetime
    student_id: int
    student_number: str
    display_name: str
    completed_experiments: int
    eligible_experiments: int
    correct_rate: MetricValue
    score_rate: MetricValue
    average_answer_time_ms: Decimal | None
    experiments: tuple[StudentExperimentPerformanceRow, ...]
    trend_rows: tuple[StudentTrendRow, ...]
    show_trend_chart: bool
    kc_rows: tuple[StudentKCComparisonRow, ...]

    @property
    def average_answer_time_display(self):
        if self.average_answer_time_ms is None:
            return "—"
        seconds = (self.average_answer_time_ms / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{seconds}s"


@dataclass(frozen=True)
class StudentExperimentQuestionRow:
    generated_at: datetime
    question_id: int
    code: str
    kcs: tuple[str, ...]
    result_label: str
    first_answer: str | None
    final_answer: str
    revision_count: int | None
    answer_time_ms: Decimal | None
    process_complete: bool

    @property
    def answer_time_display(self):
        if self.answer_time_ms is None:
            return "—"
        seconds = (self.answer_time_ms / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{seconds}s"


@dataclass(frozen=True)
class StudentExperimentDetailSnapshot:
    generated_at: datetime
    experiment_id: int
    experiment_name: str
    student_id: int
    student_number: str
    display_name: str
    is_current_member: bool
    completed: bool
    correct_rate: MetricValue
    score_rate: MetricValue
    average_answer_time_ms: Decimal | None
    rank: int | None
    rank_sample_size: int
    questions: tuple[StudentExperimentQuestionRow, ...]

    @property
    def average_answer_time_display(self):
        if self.average_answer_time_ms is None:
            return "—"
        seconds = (self.average_answer_time_ms / Decimal("1000")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return f"{seconds}s"


_TRANSITION_LABELS = (
    ("correct_to_correct", "首答正确 → 终答正确"),
    ("incorrect_to_correct", "首答错误 → 终答正确"),
    ("correct_to_incorrect", "首答正确 → 终答错误"),
    ("incorrect_to_incorrect", "首答错误 → 终答错误"),
)


def _mean(values):
    values = tuple(values)
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _task_and_question(class_group, experiment_id, question_id):
    tasks = resolve_task_facts(class_group, experiment_id=experiment_id)
    if len(tasks) != 1:
        raise LookupError("Class experiment task is unavailable.")
    task = tasks[0]
    try:
        question = QuestionSnapshot.objects.get(
            pk=question_id,
            version_id=task.release_snapshot_id,
        )
    except QuestionSnapshot.DoesNotExist as error:
        raise LookupError("Question is outside the class experiment task.") from error
    return task, question


def _cohort_context(class_group, task):
    participants = tuple(
        ExperimentVersionParticipantSnapshot.objects.filter(
            version_id=task.release_snapshot_id,
            class_snapshot_id=task.class_snapshot_id,
            student__role=User.Role.STUDENT,
        )
        .select_related("student")
        .order_by("student__student_number", "student_id")
    )
    eligible_ids = {participant.student_id for participant in participants}
    current_ids = set(
        CourseEnrollment.objects.filter(
            course_id=class_group.course_id,
            class_group=class_group,
            is_active=True,
            student__is_active=True,
            student__role=User.Role.STUDENT,
            student_id__in=eligible_ids,
        ).values_list("student_id", flat=True)
    )
    sessions = tuple(
        ExperimentSession.objects.filter(
            version_id=task.release_snapshot_id,
            class_snapshot_id=task.class_snapshot_id,
            student_id__in=eligible_ids,
        )
        .exclude(status=ExperimentSession.Status.INVALIDATED)
        .select_related("student")
    )
    return participants, eligible_ids, current_ids, sessions


def _question_attempts(question, sessions):
    session_ids = [session.pk for session in sessions]
    return tuple(
        Attempt.objects.filter(
            session_id__in=session_ids,
            assignment__snapshot=question,
        ).select_related("session__student", "assignment__snapshot")
    )


def _submission_and_grade_maps(attempts):
    attempt_ids = [attempt.pk for attempt in attempts]
    submissions = tuple(
        SubmissionEvent.objects.filter(attempt_id__in=attempt_ids).select_related(
            "final_response_snapshot"
        )
    )
    submission_by_attempt = {
        submission.attempt_id: submission for submission in submissions
    }
    grades = tuple(
        GradeRecord.objects.filter(
            submission__attempt_id__in=attempt_ids,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).select_related("submission")
    )
    grade_by_attempt = {grade.submission.attempt_id: grade for grade in grades}
    return submission_by_attempt, grade_by_attempt


def _process_map(generated_at, attempts, submission_by_attempt):
    attempt_ids = [attempt.pk for attempt in attempts]
    events_by_attempt = {attempt_id: [] for attempt_id in attempt_ids}
    for event in AssessmentEvent.objects.filter(
        attempt_id__in=attempt_ids,
        event_type__in=(
            "item_entered", "item_rendered", "item_left",
            "response_state_changed", "text_edit_observed",
            "response_commit_requested",
            "response_committed", "response_commit_rejected", "attempt_closed",
        ),
    ).order_by("attempt_id", "server_ingest_sequence_no"):
        events_by_attempt[event.attempt_id].append(event)
    for attempt_id, rows in events_by_attempt.items():
        events_by_attempt[attempt_id] = list(
            order_process_records(
                rows, fallback_time_field="server_ingest_sequence_no"
            )
        )
    snapshots_by_attempt = {attempt_id: [] for attempt_id in attempt_ids}
    for snapshot in ResponseSnapshot.objects.filter(attempt_id__in=attempt_ids).order_by(
        "attempt_id", "created_at"
    ):
        snapshots_by_attempt[snapshot.attempt_id].append(snapshot)
    observations_by_attempt = {attempt_id: [] for attempt_id in attempt_ids}
    for observation in ResponseInputObservation.objects.filter(
        attempt_id__in=attempt_ids
    ).select_related("source_event").order_by("attempt_id", "observed_at"):
        observations_by_attempt[observation.attempt_id].append(observation)

    projections = {}
    for attempt in attempts:
        submission = submission_by_attempt.get(attempt.pk)
        if submission is None:
            continue
        projection = project_attempt_process(
            generated_at=generated_at,
            attempt_id=attempt.pk,
            question_type=attempt.assignment.snapshot.question_type,
            events=events_by_attempt[attempt.pk],
            snapshots=snapshots_by_attempt[attempt.pk],
            observations=observations_by_attempt[attempt.pk],
            final_answer=submission.final_answer,
            final_snapshot_id=submission.final_response_snapshot_id,
            submitted_at=submission.committed_at,
        )
        fact_labels = {
            "item_entered": "进入题目",
            "item_rendered": "题目可交互",
            "item_left": "离开题目",
            "response_commit_requested": "请求提交",
            "response_committed": "服务器提交成功",
            "response_commit_rejected": "服务器拒绝提交",
            "attempt_closed": "作答关闭",
        }
        ordered_events = events_by_attempt[attempt.pk]
        event_rank = {id(event): index for index, event in enumerate(ordered_events)}
        response_events = iter(
            event for event in ordered_events
            if event.event_type == "response_state_changed"
        )
        text_events = iter(
            event for event in ordered_events
            if event.event_type == "text_edit_observed"
        )
        committed_event = next(
            (event for event in ordered_events if event.event_type == "response_committed"),
            None,
        )
        ranked_steps = []
        for step in projection.steps:
            source_event = None
            if step.kind == "response":
                source_event = next(response_events, None)
            elif step.kind in {"insert", "delete", "replace", "paste", "unknown"}:
                source_event = next(text_events, None)
            if source_event is not None:
                rank = event_rank[id(source_event)] + 0.1
            elif step.kind == "submit" and committed_event is not None:
                rank = event_rank[id(committed_event)] - 0.1
            else:
                rank = len(ordered_events) + len(ranked_steps)
            ranked_steps.append((rank, step, source_event))

        visit_number = 0
        for event in ordered_events:
            if event.event_type in {"response_state_changed", "text_edit_observed"}:
                continue
            value = None
            if event.event_type == "item_left" and event.payload.get("to_assignment_id"):
                value = "已前往下一题"
            elif event.event_type == "attempt_closed":
                value = close_reason_label(event.payload.get("close_reason"))
            label = fact_labels[event.event_type]
            if event.event_type == "item_entered":
                visit_number += 1
                label = f"{label}（Visit {visit_number}）"
            fact_step = ProcessStep(
                generated_at=generated_at,
                kind=event.event_type,
                label=label,
                occurred_at=event.estimated_occurred_at_utc or event.server_received_at,
                value=value,
                time_source=("synchronized_estimate" if event.estimated_occurred_at_utc else "server_received_at"),
                interval_ms=None,
            )
            ranked_steps.append((event_rank[id(event)], fact_step, event))

        merged_steps = []
        prior_source_event = None
        for _rank, step, source_event in sorted(
            ranked_steps, key=lambda row: (row[0], row[1].kind)
        ):
            interval_ms = step.interval_ms
            raw_time_source = step.time_source
            has_reliable_interval = False
            if source_event is not None:
                raw_time_source = (
                    "synchronized_estimate"
                    if source_event.estimated_occurred_at_utc
                    else "server_received_at"
                )
                if (
                    prior_source_event is not None
                    and source_event.client_instance_id
                    and source_event.client_instance_id
                    == prior_source_event.client_instance_id
                    and source_event.client_time_origin
                    == prior_source_event.client_time_origin
                    and source_event.client_monotonic_ms is not None
                    and prior_source_event.client_monotonic_ms is not None
                ):
                    interval_ms = int(
                        source_event.client_monotonic_ms
                        - prior_source_event.client_monotonic_ms
                    )
                    has_reliable_interval = True
                prior_source_event = source_event
            time_source = event_time_source_message(raw_time_source)
            if has_reliable_interval:
                time_source += "；与上一步的间隔可可靠比较"
            merged_steps.append(
                replace(
                    step,
                    time_source=time_source,
                    interval_ms=interval_ms,
                )
            )
        projections[attempt.pk] = replace(
            projection,
            steps=tuple(merged_steps),
        )
    return projections


def _answer_keys(question_type, answer):
    canonical = canonical_response_state(question_type, answer)
    if canonical is None:
        return ()
    if question_type == "multiple_choice":
        return tuple(display_text(value).strip().upper() for value in canonical)
    if question_type == "true_false":
        return (canonical,)
    return (display_text(canonical).strip().upper(),)


def _correct_keys(question):
    return set(_answer_keys(question.question_type, question.correct_answer))


def _option_rows(generated_at, question, submissions):
    if question.question_type not in {
        "single_choice",
        "multiple_choice",
        "true_false",
    }:
        return ()
    options = options_for_snapshot(question)
    if question.question_type == "true_false":
        option_pairs = (("true", "正确"), ("false", "错误"))
    else:
        option_pairs = tuple((option.key.strip().upper(), option.text) for option in options)
    counts = Counter()
    for submission in submissions:
        counts.update(_answer_keys(question.question_type, submission.final_answer))
    known = {key for key, _label in option_pairs}
    extras = tuple(
        (key, "快照中未找到该选项")
        for key in sorted(set(counts) - known)
    )
    correct = _correct_keys(question)
    return tuple(
        OptionDistributionRow(
            generated_at=generated_at,
            key=key,
            label=label,
            count=counts[key],
            is_correct=key in correct,
        )
        for key, label in (*option_pairs, *extras)
    )


def _transition_rows(generated_at, question, projections):
    if question.question_type not in {"single_choice", "true_false"}:
        return TransitionApplicability.NOT_APPLICABLE, ()
    correct = _correct_keys(question)
    counts = Counter()
    for projection in projections.values():
        if projection.completeness != ProcessCompleteness.COMPLETE:
            continue
        first_key = _answer_keys(question.question_type, projection.first_value)
        final_key = _answer_keys(question.question_type, projection.final_value)
        if len(first_key) != 1 or len(final_key) != 1:
            continue
        first_correct = first_key[0] in correct
        final_correct = final_key[0] in correct
        key = (
            ("correct" if first_correct else "incorrect")
            + "_to_"
            + ("correct" if final_correct else "incorrect")
        )
        counts[key] += 1
    return TransitionApplicability.APPLICABLE, tuple(
        TransitionRow(
            generated_at=generated_at,
            key=key,
            label=label,
            count=counts[key],
        )
        for key, label in _TRANSITION_LABELS
    )


def _result_label(grade):
    if grade is None or grade.is_correct is None:
        return "待评分"
    return "正确" if grade.is_correct else "错误"


def build_question_detail(class_group, *, experiment_id, question_id):
    with analytics_read_transaction() as generated_at:
        task, question = _task_and_question(
            class_group, experiment_id, question_id
        )
        participants, _eligible_ids, current_ids, sessions = _cohort_context(
            class_group, task
        )
        attempts = _question_attempts(question, sessions)
        submission_by_attempt, grade_by_attempt = _submission_and_grade_maps(attempts)
        projections = _process_map(generated_at, attempts, submission_by_attempt)
        submissions = tuple(submission_by_attempt.values())
        evaluated = tuple(
            grade for grade in grade_by_attempt.values() if grade.is_correct is not None
        )
        correct_count = sum(grade.is_correct is True for grade in evaluated)
        timing = summarize_question_timing(
            generated_at=generated_at,
            attempts=attempts,
            submissions_by_attempt=submission_by_attempt,
        )
        transition_applicability, transition_rows = _transition_rows(
            generated_at, question, projections
        )
        options = options_for_snapshot(question)
        participants_rows = []
        for attempt in sorted(
            attempts,
            key=lambda item: (
                item.session.student.student_number,
                item.session.student_id,
            ),
        ):
            submission = submission_by_attempt.get(attempt.pk)
            if submission is None:
                continue
            student = attempt.session.student
            projection = projections.get(attempt.pk)
            participants_rows.append(
                QuestionParticipantRow(
                    generated_at=generated_at,
                    student_id=student.pk,
                    student_number=student.student_number or student.username,
                    display_name=student.shown_name,
                    is_current_member=student.pk in current_ids,
                    result_label=_result_label(grade_by_attempt.get(attempt.pk)),
                    final_answer=format_answer(
                        question.question_type,
                        submission.final_answer,
                        options,
                    )
                    or "（空）",
                    process_complete=(
                        projection is not None
                        and projection.completeness == ProcessCompleteness.COMPLETE
                    ),
                )
            )
        complete_chains = sum(
            projection.completeness == ProcessCompleteness.COMPLETE
            for projection in projections.values()
        )
        return QuestionDetailSnapshot(
            generated_at=generated_at,
            experiment_id=task.experiment_id,
            experiment_name=task.experiment_name,
            question_id=question.pk,
            code=question.code,
            text=display_text(question.text),
            question_type=question.question_type,
            kcs=tuple(dict.fromkeys(question.all_kcs or [question.primary_kc])),
            eligible_count=len(participants),
            current_member_count=len(current_ids),
            historical_participant_count=len(participants) - len(current_ids),
            answer_count=len(submissions),
            correct_count=correct_count,
            incorrect_count=len(evaluated) - correct_count,
            correct_rate=MetricValue.fraction(
                generated_at=generated_at,
                numerator=correct_count,
                denominator=len(evaluated),
            ),
            timing=timing,
            chain_coverage=MetricValue.fraction(
                generated_at=generated_at,
                numerator=complete_chains,
                denominator=len(submissions),
            ),
            option_rows=_option_rows(generated_at, question, submissions),
            transition_applicability=transition_applicability,
            transition_rows=transition_rows,
            participants=tuple(participants_rows),
        )


def build_attempt_process_detail(
    class_group,
    *,
    experiment_id,
    question_id,
    student_id,
):
    with analytics_read_transaction() as generated_at:
        task, question = _task_and_question(
            class_group, experiment_id, question_id
        )
        participants, eligible_ids, current_ids, sessions = _cohort_context(
            class_group, task
        )
        if student_id not in eligible_ids:
            raise LookupError("Student is outside the frozen participant cohort.")
        student_by_id = {
            participant.student_id: participant.student for participant in participants
        }
        session = next(
            (session for session in sessions if session.student_id == student_id),
            None,
        )
        if session is None:
            raise LookupError("Student has no valid session for this class task.")
        attempts = _question_attempts(question, (session,))
        if len(attempts) != 1:
            raise LookupError("Student attempt is unavailable.")
        attempt = attempts[0]
        submission_by_attempt, grade_by_attempt = _submission_and_grade_maps(attempts)
        submission = submission_by_attempt.get(attempt.pk)
        if submission is None:
            raise LookupError("Student has no formal submission for this question.")
        process = _process_map(
            generated_at, attempts, submission_by_attempt
        )[attempt.pk]
        grade = grade_by_attempt.get(attempt.pk)
        score_display = "—"
        if grade is not None and grade.score is not None and grade.max_score is not None:
            score_display = f"{grade.score} / {grade.max_score}"
        student = student_by_id[student_id]
        coverage = (
            RecordingCoverageAttempt.objects.filter(
                attempt=attempt,
                run__run_status="completed",
            ).select_related("run").order_by("-run__generated_at").first()
        )
        pending_finalization = coverage is None and (
            session.status == ExperimentSession.Status.IN_PROGRESS
            or (
                session.terminal_at is not None
                and (generated_at - session.terminal_at).total_seconds() < 300
            )
        )
        fallback_status = (
            "pending_finalization" if pending_finalization else "legacy_unknown"
        )
        fallback_reasons = (
            ("pending_finalization",)
            if pending_finalization else ("coverage_run_missing",)
        )
        coverage_status = coverage.overall_status if coverage else fallback_status
        coverage_time_quality = coverage.time_quality if coverage else "unknown"
        missing_reasons = tuple(coverage.missing_reasons) if coverage else fallback_reasons
        status_message = coverage_status_message(coverage_status)
        time_message = time_quality_message(coverage_time_quality)
        return AttemptProcessDetail(
            generated_at=generated_at,
            experiment_id=task.experiment_id,
            experiment_name=task.experiment_name,
            question_id=question.pk,
            question_code=question.code,
            question_text=display_text(question.text),
            student_id=student.pk,
            student_number=student.student_number or student.username,
            display_name=student.shown_name,
            is_current_member=student.pk in current_ids,
            result_label=_result_label(grade),
            score_display=score_display,
            process=process,
            coverage_status_title=status_message.title,
            coverage_status_description=status_message.description,
            coverage_time_title=time_message.title,
            coverage_time_description=time_message.description,
            coverage_reason_labels=coverage_reason_labels(missing_reasons),
            has_cross_instance_order_uncertainty=(
                "cross_instance_order_uncertain" in missing_reasons
            ),
        )


def _correct_metric(generated_at, grade_attempt_pairs):
    evaluated = tuple(
        grade for grade, _attempt in grade_attempt_pairs if grade.is_correct is not None
    )
    return MetricValue.fraction(
        generated_at=generated_at,
        numerator=sum(grade.is_correct is True for grade in evaluated),
        denominator=len(evaluated),
    )


def _score_metric(generated_at, grade_attempt_pairs):
    pairs = tuple(
        (grade.score, grade.max_score)
        for grade, _attempt in grade_attempt_pairs
        if grade.score is not None and grade.max_score is not None
    )
    return MetricValue.fraction(
        generated_at=generated_at,
        numerator=sum((score for score, _maximum in pairs), Decimal("0")),
        denominator=sum((maximum for _score, maximum in pairs), Decimal("0")),
    )


def _class_student_fact_rows(class_group, tasks):
    roster = tuple(
        CourseEnrollment.objects.filter(
            course_id=class_group.course_id,
            class_group=class_group,
            is_active=True,
            student__is_active=True,
            student__role=User.Role.STUDENT,
        )
        .select_related("student")
        .order_by("student__student_number", "student_id")
    )
    roster_ids = {enrollment.student_id for enrollment in roster}
    task_by_pair = {
        (task.release_snapshot_id, task.class_snapshot_id): task for task in tasks
    }
    participants = tuple(
        participant
        for participant in ExperimentVersionParticipantSnapshot.objects.filter(
            version_id__in={task.release_snapshot_id for task in tasks},
            student_id__in=roster_ids,
        ).only("version_id", "class_snapshot_id", "student_id")
        if (participant.version_id, participant.class_snapshot_id) in task_by_pair
    )
    eligible_pairs = {
        (participant.version_id, participant.student_id) for participant in participants
    }
    sessions = tuple(
        session
        for session in ExperimentSession.objects.filter(
            version_id__in={task.release_snapshot_id for task in tasks},
            student_id__in=roster_ids,
        ).only("id", "version_id", "class_snapshot_id", "student_id", "status")
        if (session.version_id, session.class_snapshot_id) in task_by_pair
        and (session.version_id, session.student_id) in eligible_pairs
        and session.status != ExperimentSession.Status.INVALIDATED
    )
    session_by_id = {session.pk: session for session in sessions}
    attempts = tuple(
        attempt
        for attempt in Attempt.objects.filter(session_id__in=session_by_id)
        .select_related("assignment__snapshot")
        if attempt.assignment.snapshot.version_id
        == session_by_id[attempt.session_id].version_id
    )
    attempt_by_id = {attempt.pk: attempt for attempt in attempts}
    submissions = tuple(
        SubmissionEvent.objects.filter(attempt_id__in=attempt_by_id)
    )
    submitted_attempt_ids = {submission.attempt_id for submission in submissions}
    grades = tuple(
        grade
        for grade in GradeRecord.objects.filter(
            submission__attempt_id__in=attempt_by_id,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).select_related("submission")
        if grade.submission.attempt_id in attempt_by_id
    )
    grade_attempt_pairs = tuple(
        (grade, attempt_by_id[grade.submission.attempt_id]) for grade in grades
    )
    return (
        roster,
        eligible_pairs,
        sessions,
        attempts,
        submitted_attempt_ids,
        grade_attempt_pairs,
    )


def _rank_for_task(task, roster, sessions, attempts, grade_attempt_pairs):
    session_by_id = {session.pk: session for session in sessions}
    task_attempts = tuple(
        attempt
        for attempt in attempts
        if session_by_id[attempt.session_id].version_id == task.release_snapshot_id
    )
    attempts_by_student = Counter(
        session_by_id[attempt.session_id].student_id for attempt in task_attempts
    )
    scores_by_student = {}
    for grade, attempt in grade_attempt_pairs:
        session = session_by_id[attempt.session_id]
        if (
            session.version_id != task.release_snapshot_id
            or grade.score is None
            or grade.max_score is None
        ):
            continue
        score, maximum = scores_by_student.get(
            session.student_id, (Decimal("0"), Decimal("0"))
        )
        scores_by_student[session.student_id] = (
            score + grade.score,
            maximum + grade.max_score,
        )
    values = []
    for enrollment in roster:
        student_id = enrollment.student_id
        score, maximum = scores_by_student.get(
            student_id, (Decimal("0"), Decimal("0"))
        )
        graded_count = sum(
            1
            for grade, attempt in grade_attempt_pairs
            if session_by_id[attempt.session_id].student_id == student_id
            and session_by_id[attempt.session_id].version_id == task.release_snapshot_id
            and grade.score is not None
            and grade.max_score is not None
        )
        comparable = (
            attempts_by_student[student_id] > 0
            and graded_count == attempts_by_student[student_id]
            and maximum > 0
        )
        values.append(
            (
                student_id,
                score / maximum if comparable else None,
                enrollment.student.student_number or enrollment.student.username,
            )
        )
    return competition_ranks(values)


def build_student_detail(class_group, *, student_id):
    with analytics_read_transaction() as generated_at:
        tasks = resolve_task_facts(class_group)
        (
            roster,
            eligible_pairs,
            sessions,
            attempts,
            submitted_attempt_ids,
            grade_attempt_pairs,
        ) = _class_student_fact_rows(class_group, tasks)
        enrollment = next(
            (item for item in roster if item.student_id == student_id), None
        )
        if enrollment is None:
            raise LookupError("Student is not a current class member.")
        student = enrollment.student
        session_by_id = {session.pk: session for session in sessions}
        sessions_by_student = {
            current_id: tuple(
                session for session in sessions if session.student_id == current_id
            )
            for current_id in {item.student_id for item in roster}
        }
        grades_by_student = {
            current_id: tuple(
                (grade, attempt)
                for grade, attempt in grade_attempt_pairs
                if session_by_id[attempt.session_id].student_id == current_id
            )
            for current_id in {item.student_id for item in roster}
        }

        experiment_rows = []
        trend_rows = []
        for task in tasks:
            target_pairs = tuple(
                (grade, attempt)
                for grade, attempt in grades_by_student[student_id]
                if session_by_id[attempt.session_id].version_id
                == task.release_snapshot_id
            )
            target_attempts = tuple(
                attempt
                for attempt in attempts
                if session_by_id[attempt.session_id].student_id == student_id
                and session_by_id[attempt.session_id].version_id
                == task.release_snapshot_id
            )
            target_correct = _correct_metric(generated_at, target_pairs)
            class_ratios = []
            for current in roster:
                current_pairs = tuple(
                    (grade, attempt)
                    for grade, attempt in grades_by_student[current.student_id]
                    if session_by_id[attempt.session_id].version_id
                    == task.release_snapshot_id
                )
                ratio = _correct_metric(generated_at, current_pairs).ratio
                if ratio is not None:
                    class_ratios.append(ratio)
            macro = _mean(class_ratios)
            class_average = MetricValue.fraction(
                generated_at=generated_at,
                numerator=macro if macro is not None else 0,
                denominator=1 if macro is not None else 0,
                sample_size=len(class_ratios),
            )
            ranks = _rank_for_task(
                task, roster, sessions, attempts, grade_attempt_pairs
            )
            durations = tuple(
                Decimal(attempt.first_to_submit_duration_ms)
                for attempt in target_attempts
                if attempt.pk in submitted_attempt_ids
                and attempt.first_to_submit_duration_ms is not None
                and attempt.first_to_submit_duration_ms >= 0
            )
            experiment_rows.append(
                StudentExperimentPerformanceRow(
                    generated_at=generated_at,
                    experiment_id=task.experiment_id,
                    name=task.experiment_name,
                    completed=any(
                        session.version_id == task.release_snapshot_id
                        and session.status == ExperimentSession.Status.COMPLETED
                        for session in sessions_by_student[student_id]
                    ),
                    correct_rate=target_correct,
                    score_rate=_score_metric(generated_at, target_pairs),
                    rank=ranks.get(student_id),
                    rank_sample_size=len(ranks),
                    average_answer_time_ms=_mean(durations),
                )
            )
            if target_correct.ratio is not None and class_average.ratio is not None:
                trend_rows.append(
                    StudentTrendRow(
                        generated_at=generated_at,
                        experiment_id=task.experiment_id,
                        name=task.experiment_name,
                        student_correct_rate=target_correct,
                        class_average_correct_rate=class_average,
                        x=Decimal("0"),
                        student_y=Decimal("0"),
                        class_y=Decimal("0"),
                    )
                )

        kc_student_counts = Counter()
        kc_class_counts = {}
        for current in roster:
            per_student = Counter()
            for grade, attempt in grades_by_student[current.student_id]:
                if grade.is_correct is None:
                    continue
                question = attempt.assignment.snapshot
                for kc in dict.fromkeys(question.all_kcs or [question.primary_kc]):
                    if not kc:
                        continue
                    per_student[(kc, "total")] += 1
                    per_student[(kc, "correct")] += grade.is_correct is True
                    if current.student_id == student_id:
                        kc_student_counts[(kc, "total")] += 1
                        kc_student_counts[(kc, "correct")] += grade.is_correct is True
            for kc in {key[0] for key in per_student}:
                total = per_student[(kc, "total")]
                if total:
                    kc_class_counts.setdefault(kc, []).append(
                        Decimal(per_student[(kc, "correct")]) / Decimal(total)
                    )
        kc_rows = []
        for kc in sorted(kc_class_counts):
            class_ratios = kc_class_counts[kc]
            macro = _mean(class_ratios)
            student_metric = MetricValue.fraction(
                generated_at=generated_at,
                numerator=kc_student_counts[(kc, "correct")],
                denominator=kc_student_counts[(kc, "total")],
            )
            class_metric = MetricValue.fraction(
                generated_at=generated_at,
                numerator=macro if macro is not None else 0,
                denominator=1 if macro is not None else 0,
                sample_size=len(class_ratios),
            )
            kc_rows.append(
                StudentKCComparisonRow(
                    generated_at=generated_at,
                    name=kc,
                    comparison=StudentComparison(
                        generated_at=generated_at,
                        metric_key="correct_rate",
                        student_value=student_metric,
                        class_average=class_metric,
                        sample_size=len(class_ratios),
                    ),
                )
            )

        target_pairs = grades_by_student[student_id]
        target_attempts = tuple(
            attempt
            for attempt in attempts
            if session_by_id[attempt.session_id].student_id == student_id
        )
        durations = tuple(
            Decimal(attempt.first_to_submit_duration_ms)
            for attempt in target_attempts
            if attempt.pk in submitted_attempt_ids
            and attempt.first_to_submit_duration_ms is not None
            and attempt.first_to_submit_duration_ms >= 0
        )
        eligible_versions = {
            version_id
            for version_id, eligible_student_id in eligible_pairs
            if eligible_student_id == student_id
        }
        completed_versions = {
            session.version_id
            for session in sessions_by_student[student_id]
            if session.status == ExperimentSession.Status.COMPLETED
        }
        chronological_trends = tuple(reversed(trend_rows))
        if len(chronological_trends) >= 2:
            plotted_trends = tuple(
                StudentTrendRow(
                    generated_at=row.generated_at,
                    experiment_id=row.experiment_id,
                    name=row.name,
                    student_correct_rate=row.student_correct_rate,
                    class_average_correct_rate=row.class_average_correct_rate,
                    x=(
                        Decimal("5")
                        + Decimal(index)
                        * Decimal("90")
                        / Decimal(len(chronological_trends) - 1)
                    ).quantize(Decimal("0.1")),
                    student_y=(
                        Decimal("95")
                        - row.student_correct_rate.ratio * Decimal("90")
                    ).quantize(Decimal("0.1")),
                    class_y=(
                        Decimal("95")
                        - row.class_average_correct_rate.ratio * Decimal("90")
                    ).quantize(Decimal("0.1")),
                )
                for index, row in enumerate(chronological_trends)
            )
        else:
            plotted_trends = chronological_trends
        return StudentDetailSnapshot(
            generated_at=generated_at,
            student_id=student.pk,
            student_number=student.student_number or student.username,
            display_name=student.shown_name,
            completed_experiments=len(completed_versions & eligible_versions),
            eligible_experiments=len(eligible_versions),
            correct_rate=_correct_metric(generated_at, target_pairs),
            score_rate=_score_metric(generated_at, target_pairs),
            average_answer_time_ms=_mean(durations),
            experiments=tuple(experiment_rows),
            trend_rows=plotted_trends,
            show_trend_chart=len(trend_rows) >= 2,
            kc_rows=tuple(
                sorted(
                    kc_rows,
                    key=lambda row: (
                        row.comparison.student_value.ratio is None,
                        row.comparison.student_value.ratio or Decimal("0"),
                        row.name,
                    ),
                )
            ),
        )


def build_student_experiment_detail(
    class_group,
    *,
    experiment_id,
    student_id,
):
    with analytics_read_transaction() as generated_at:
        tasks = resolve_task_facts(class_group, experiment_id=experiment_id)
        if len(tasks) != 1:
            raise LookupError("Class experiment task is unavailable.")
        task = tasks[0]
        participants, eligible_ids, current_ids, sessions = _cohort_context(
            class_group, task
        )
        if student_id not in eligible_ids:
            raise LookupError("Student is outside the frozen participant cohort.")
        student = next(
            participant.student
            for participant in participants
            if participant.student_id == student_id
        )
        session_by_id = {session.pk: session for session in sessions}
        attempts = tuple(
            Attempt.objects.filter(session_id__in=session_by_id).select_related(
                "session__student", "assignment__snapshot"
            )
        )
        attempt_by_id = {attempt.pk: attempt for attempt in attempts}
        submission_by_attempt, grade_by_attempt = _submission_and_grade_maps(attempts)
        grade_attempt_pairs = tuple(
            (grade, attempt_by_id[attempt_id])
            for attempt_id, grade in grade_by_attempt.items()
        )
        cohort_rows = tuple(
            SimpleEnrollment(participant.student_id, participant.student)
            for participant in participants
        )
        ranks = _rank_for_task(
            task, cohort_rows, sessions, attempts, grade_attempt_pairs
        )
        target_session = next(
            (session for session in sessions if session.student_id == student_id), None
        )
        target_attempts = tuple(
            attempt
            for attempt in attempts
            if session_by_id[attempt.session_id].student_id == student_id
        )
        target_attempt_ids = {attempt.pk for attempt in target_attempts}
        target_submission_map = {
            attempt_id: submission
            for attempt_id, submission in submission_by_attempt.items()
            if attempt_id in target_attempt_ids
        }
        projections = _process_map(
            generated_at, target_attempts, target_submission_map
        )
        target_pairs = tuple(
            (grade_by_attempt[attempt.pk], attempt)
            for attempt in target_attempts
            if attempt.pk in grade_by_attempt
        )
        question_rows = []
        for attempt in sorted(
            target_attempts,
            key=lambda item: item.assignment.assigned_sequence_index,
        ):
            question = attempt.assignment.snapshot
            submission = target_submission_map.get(attempt.pk)
            if submission is None:
                continue
            projection = projections.get(attempt.pk)
            grade = grade_by_attempt.get(attempt.pk)
            question_rows.append(
                StudentExperimentQuestionRow(
                    generated_at=generated_at,
                    question_id=question.pk,
                    code=question.code,
                    kcs=tuple(dict.fromkeys(question.all_kcs or [question.primary_kc])),
                    result_label=_result_label(grade),
                    first_answer=projection.first_value if projection else None,
                    final_answer=(
                        projection.final_value
                        if projection is not None and projection.final_value
                        else format_answer(
                            question.question_type,
                            submission.final_answer,
                            options_for_snapshot(question),
                        )
                        or "（空）"
                    ),
                    revision_count=(
                        projection.revision_count if projection else None
                    ),
                    answer_time_ms=(
                        Decimal(attempt.first_to_submit_duration_ms)
                        if attempt.first_to_submit_duration_ms is not None
                        and attempt.first_to_submit_duration_ms >= 0
                        else None
                    ),
                    process_complete=(
                        projection is not None
                        and projection.completeness == ProcessCompleteness.COMPLETE
                    ),
                )
            )
        durations = tuple(
            row.answer_time_ms
            for row in question_rows
            if row.answer_time_ms is not None
        )
        return StudentExperimentDetailSnapshot(
            generated_at=generated_at,
            experiment_id=task.experiment_id,
            experiment_name=task.experiment_name,
            student_id=student.pk,
            student_number=student.student_number or student.username,
            display_name=student.shown_name,
            is_current_member=student.pk in current_ids,
            completed=(
                target_session is not None
                and target_session.status == ExperimentSession.Status.COMPLETED
            ),
            correct_rate=_correct_metric(generated_at, target_pairs),
            score_rate=_score_metric(generated_at, target_pairs),
            average_answer_time_ms=_mean(durations),
            rank=ranks.get(student_id),
            rank_sample_size=len(ranks),
            questions=tuple(question_rows),
        )


@dataclass(frozen=True)
class SimpleEnrollment:
    student_id: int
    student: object
