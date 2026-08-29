from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from math import ceil
from typing import NamedTuple

from django.db.models import Q

from apps.accounts.models import CourseEnrollment, User
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    SubmissionEvent,
)
from apps.experiments.models import ExperimentVersionParticipantSnapshot, QuestionSnapshot

from .contracts import (
    ClassAnalysisScope,
    ClassCenterSnapshot,
    ClassOverview,
    DistributionBin,
    ExperimentOption,
    ExperimentParticipantRow,
    ExperimentSummaryRow,
    KCRateRow,
    MetricValue,
    ParticipationBreakdown,
    QuestionSummaryRow,
    StudentPage,
    StudentSummaryRow,
)
from .comparisons import competition_ranks
from .scope import analytics_read_transaction, resolve_task_facts


_DISTRIBUTION_BINS = (
    ("lt_60", "<60%"),
    ("60_69", "60–69%"),
    ("70_79", "70–79%"),
    ("80_89", "80–89%"),
    ("90_100", "90–100%"),
    ("no_data", "无数据"),
)
_STUDENT_SORTS = {
    "student_number",
    "name",
    "correct_rate",
    "score_rate",
    "completion_rate",
    "average_time",
    "rank",
}
_STUDENT_STATUSES = {"completed", "in_progress", "not_participated"}


class _AttemptFact(NamedTuple):
    id: int
    session_id: int
    first_to_submit_duration_ms: int | None
    snapshot_id: int
    snapshot_version_id: int
    snapshot_primary_kc: str
    snapshot_all_kcs: list


class _GradeFact(NamedTuple):
    attempt_id: int
    is_correct: bool | None
    score: Decimal | None
    max_score: Decimal | None


def _metric(generated_at, numerator, denominator, *, sample_size=None):
    return MetricValue.fraction(
        generated_at=generated_at,
        numerator=numerator,
        denominator=denominator,
        sample_size=sample_size,
    )


def _mean(values):
    values = list(values)
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _student_status(eligible_versions, completed_versions, session_versions):
    if not session_versions:
        return "not_participated"
    if eligible_versions and eligible_versions.issubset(completed_versions):
        return "completed"
    return "in_progress"


def _distribution_key(ratio):
    if ratio is None:
        return "no_data"
    percentage = ratio * Decimal("100")
    if percentage < 60:
        return "lt_60"
    if percentage < 70:
        return "60_69"
    if percentage < 80:
        return "70_79"
    if percentage < 90:
        return "80_89"
    return "90_100"


def _sort_student_rows(rows, sort):
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    if key not in _STUDENT_SORTS:
        key = "student_number"
        descending = False

    if key == "student_number":
        return sorted(
            rows,
            key=lambda row: (row["student_number"], row["student_id"]),
            reverse=descending,
        )
    if key == "name":
        return sorted(
            rows,
            key=lambda row: (row["display_name"], row["student_number"]),
            reverse=descending,
        )

    value_functions = {
        "correct_rate": lambda row: row["correct_rate"].ratio,
        "score_rate": lambda row: row["score_rate"].ratio,
        "completion_rate": lambda row: (
            Decimal(row["completed_experiments"])
            / Decimal(row["eligible_experiments"])
            if row["eligible_experiments"]
            else None
        ),
        "average_time": lambda row: row["average_answer_time_ms"],
        "rank": lambda row: row["rank"],
    }
    value_for = value_functions[key]
    comparable = [row for row in rows if value_for(row) is not None]
    missing = [row for row in rows if value_for(row) is None]
    comparable.sort(key=lambda row: row["student_number"])
    comparable.sort(key=value_for, reverse=descending)
    missing.sort(key=lambda row: row["student_number"])
    return comparable + missing


def build_class_center_snapshot(
    class_group,
    *,
    experiment_id=None,
    query="",
    sort="student_number",
    status=None,
    page=1,
    page_size=25,
):
    page_size = min(max(int(page_size), 1), 100)
    page = max(int(page), 1)
    query = (query or "").strip().casefold()
    status = status if status in _STUDENT_STATUSES else None

    with analytics_read_transaction() as generated_at:
        all_tasks = resolve_task_facts(class_group)
        tasks = tuple(
            task
            for task in all_tasks
            if experiment_id is None or task.experiment_id == experiment_id
        )
        roster = list(
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
        version_ids = {task.release_snapshot_id for task in tasks}
        participant_records = tuple(
            participant
            for participant in ExperimentVersionParticipantSnapshot.objects.filter(
                version_id__in=version_ids,
            ).select_related("student")
            if (participant.version_id, participant.class_snapshot_id) in task_by_pair
        )
        eligible_pairs = {
            (participant.version_id, participant.student_id)
            for participant in participant_records
        }
        eligible_student_ids = {
            participant.student_id for participant in participant_records
        }
        sessions = [
            session
            for session in ExperimentSession.objects.filter(
                version_id__in=version_ids,
                student_id__in=eligible_student_ids,
            ).only("id", "version_id", "student_id", "class_snapshot_id", "status")
            if (session.version_id, session.class_snapshot_id) in task_by_pair
            and (session.version_id, session.student_id) in eligible_pairs
            and session.status != ExperimentSession.Status.INVALIDATED
        ]
        session_by_id = {session.pk: session for session in sessions}
        session_ids = tuple(session_by_id)
        attempts = tuple(
            _AttemptFact(*row)
            for row in
            Attempt.objects.filter(session_id__in=session_ids)
            .values_list(
                "id",
                "session_id",
                "first_to_submit_duration_ms",
                "assignment__snapshot_id",
                "assignment__snapshot__version_id",
                "assignment__snapshot__primary_kc",
                "assignment__snapshot__all_kcs",
            )
        )
        valid_attempts = {
            attempt.id: attempt
            for attempt in attempts
            if attempt.snapshot_version_id == session_by_id[attempt.session_id].version_id
        }
        submitted_attempt_ids = set(
            SubmissionEvent.objects.filter(attempt_id__in=valid_attempts).values_list(
                "attempt_id", flat=True
            )
        )
        grades = tuple(
            _GradeFact(*row)
            for row in GradeRecord.objects.filter(
                submission__attempt_id__in=valid_attempts,
                status=GradeRecord.Status.COMPLETED,
                validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            ).values_list(
                "submission__attempt_id",
                "is_correct",
                "score",
                "max_score",
            )
            if row[0] in valid_attempts
        )

        sessions_by_student = defaultdict(list)
        completed_versions_by_student = defaultdict(set)
        session_versions_by_student = defaultdict(set)
        for session in sessions:
            sessions_by_student[session.student_id].append(session)
            session_versions_by_student[session.student_id].add(session.version_id)
            if session.status == ExperimentSession.Status.COMPLETED:
                completed_versions_by_student[session.student_id].add(session.version_id)

        eligible_versions_by_student = defaultdict(set)
        for version_id, student_id in eligible_pairs:
            eligible_versions_by_student[student_id].add(version_id)

        grades_by_student = defaultdict(list)
        grades_by_version = defaultdict(list)
        grades_by_snapshot = defaultdict(list)
        submitted_attempts_by_student = defaultdict(list)
        for attempt_id in submitted_attempt_ids:
            attempt = valid_attempts[attempt_id]
            session = session_by_id[attempt.session_id]
            submitted_attempts_by_student[session.student_id].append(attempt)
        for grade in grades:
            attempt = valid_attempts[grade.attempt_id]
            session = session_by_id[attempt.session_id]
            grades_by_student[session.student_id].append((grade, attempt))
            grades_by_version[session.version_id].append((grade, attempt, session))
            grades_by_snapshot[attempt.snapshot_id].append(
                (grade, attempt, session)
            )

        assigned_counts_by_student = Counter(
            session_by_id[attempt.session_id].student_id
            for attempt in valid_attempts.values()
        )

        student_values = []
        for enrollment in roster:
            student = enrollment.student
            evaluated = [
                (grade, attempt)
                for grade, attempt in grades_by_student[student.pk]
                if grade.is_correct is not None
            ]
            correct_count = sum(grade.is_correct is True for grade, _ in evaluated)
            score_pairs = [
                (grade.score, grade.max_score)
                for grade, _ in grades_by_student[student.pk]
                if grade.score is not None and grade.max_score is not None
            ]
            durations = [
                Decimal(attempt.first_to_submit_duration_ms)
                for attempt in submitted_attempts_by_student[student.pk]
                if attempt.first_to_submit_duration_ms is not None
                and attempt.first_to_submit_duration_ms >= 0
            ]
            eligible_versions = eligible_versions_by_student[student.pk]
            completed_versions = completed_versions_by_student[student.pk]
            assigned_count = assigned_counts_by_student[student.pk]
            rank_ratio = None
            if (
                experiment_id is not None
                and assigned_count > 0
                and len(score_pairs) == assigned_count
                and sum((maximum for _, maximum in score_pairs), Decimal("0")) > 0
            ):
                rank_ratio = sum((score for score, _ in score_pairs), Decimal("0")) / sum(
                    (maximum for _, maximum in score_pairs), Decimal("0")
                )
            student_values.append(
                {
                    "student_id": student.pk,
                    "student_number": student.student_number or student.username,
                    "display_name": student.shown_name,
                    "completed_experiments": len(completed_versions),
                    "eligible_experiments": len(eligible_versions),
                    "correct_rate": _metric(
                        generated_at, correct_count, len(evaluated)
                    ),
                    "score_rate": _metric(
                        generated_at,
                        sum((score for score, _ in score_pairs), Decimal("0")),
                        sum((maximum for _, maximum in score_pairs), Decimal("0")),
                    ),
                    "average_answer_time_ms": _mean(durations),
                    "status": _student_status(
                        eligible_versions,
                        completed_versions,
                        session_versions_by_student[student.pk],
                    ),
                    "rank_ratio": rank_ratio,
                    "rank": None,
                }
            )

        ranks = (
            competition_ranks(
                (
                    row["student_id"],
                    row["rank_ratio"],
                    row["student_number"],
                )
                for row in student_values
            )
            if experiment_id is not None
            else {}
        )
        for row in student_values:
            row["rank"] = ranks.get(row["student_id"])

        comparable_ratios = [
            row["correct_rate"].ratio
            for row in student_values
            if row["correct_rate"].ratio is not None
        ]
        macro_ratio = _mean(comparable_ratios)
        class_average = _metric(
            generated_at,
            macro_ratio if macro_ratio is not None else 0,
            1 if macro_ratio is not None else 0,
            sample_size=len(comparable_ratios),
        )
        distribution_counts = {key: 0 for key, _ in _DISTRIBUTION_BINS}
        for row in student_values:
            distribution_counts[_distribution_key(row["correct_rate"].ratio)] += 1

        if query:
            student_values = [
                row
                for row in student_values
                if query in row["student_number"].casefold()
                or query in row["display_name"].casefold()
            ]
        if status:
            student_values = [row for row in student_values if row["status"] == status]
        student_values = _sort_student_rows(student_values, sort)
        total_count = len(student_values)
        page_count = max(1, ceil(total_count / page_size))
        page = min(page, page_count)
        start = (page - 1) * page_size
        page_values = student_values[start : start + page_size]
        student_rows = tuple(
            StudentSummaryRow(
                generated_at=generated_at,
                student_id=row["student_id"],
                student_number=row["student_number"],
                display_name=row["display_name"],
                completed_experiments=row["completed_experiments"],
                eligible_experiments=row["eligible_experiments"],
                correct_rate=row["correct_rate"],
                score_rate=row["score_rate"],
                average_answer_time_ms=row["average_answer_time_ms"],
                status=row["status"],
                rank=row["rank"],
            )
            for row in page_values
        )

        all_evaluated = [
            grade
            for student_id in roster_ids
            for grade, _attempt in grades_by_student[student_id]
            if grade.is_correct is not None
        ]
        total_correct = sum(grade.is_correct is True for grade in all_evaluated)
        completed_units = sum(
            len(completed_versions_by_student[student_id])
            for student_id in roster_ids
        )
        eligible_units = len(eligible_pairs)
        participated_student_ids = {
            session.student_id for session in sessions if session.student_id in roster_ids
        }
        participation_counts = {key: 0 for key in _STUDENT_STATUSES}
        for enrollment in roster:
            participation_counts[
                _student_status(
                    eligible_versions_by_student[enrollment.student_id],
                    completed_versions_by_student[enrollment.student_id],
                    session_versions_by_student[enrollment.student_id],
                )
            ] += 1

        kc_counts = defaultdict(lambda: [0, 0])
        for grade in all_evaluated:
            attempt = valid_attempts[grade.attempt_id]
            kcs = tuple(
                dict.fromkeys(
                    attempt.snapshot_all_kcs or [attempt.snapshot_primary_kc]
                )
            )
            for kc in (kc for kc in kcs if kc):
                kc_counts[kc][1] += 1
                kc_counts[kc][0] += grade.is_correct is True
        kc_rows = tuple(
            sorted(
                (
                    KCRateRow(
                        generated_at=generated_at,
                        name=name,
                        correct_rate=_metric(generated_at, counts[0], counts[1]),
                    )
                    for name, counts in kc_counts.items()
                ),
                key=lambda row: (row.correct_rate.ratio, row.name),
            )
        )

        experiment_rows = []
        for task in tasks:
            eligible_students = {
                student_id
                for version_id, student_id in eligible_pairs
                if version_id == task.release_snapshot_id
            }
            task_sessions = [
                session
                for session in sessions
                if session.version_id == task.release_snapshot_id
            ]
            task_grades = [
                grade
                for grade, _attempt, _session in grades_by_version[
                    task.release_snapshot_id
                ]
                if grade.is_correct is not None
            ]
            score_pairs = [
                (grade.score, grade.max_score)
                for grade, _attempt, _session in grades_by_version[
                    task.release_snapshot_id
                ]
                if grade.score is not None and grade.max_score is not None
            ]
            experiment_rows.append(
                ExperimentSummaryRow(
                    generated_at=generated_at,
                    experiment_id=task.experiment_id,
                    name=task.experiment_name,
                    is_live=task.experiment_status == "published",
                    published_at=task.published_at,
                    eligible_count=len(eligible_students),
                    participated_count=len({session.student_id for session in task_sessions}),
                    completed_count=len(
                        {
                            session.student_id
                            for session in task_sessions
                            if session.status == ExperimentSession.Status.COMPLETED
                        }
                    ),
                    correct_rate=_metric(
                        generated_at,
                        sum(grade.is_correct is True for grade in task_grades),
                        len(task_grades),
                    ),
                    score_rate=_metric(
                        generated_at,
                        sum((score for score, _ in score_pairs), Decimal("0")),
                        sum((maximum for _, maximum in score_pairs), Decimal("0")),
                    ),
                )
            )

        experiment_participants = []
        if experiment_id is not None and tasks:
            selected_version_id = tasks[0].release_snapshot_id
            selected_participants = tuple(
                participant
                for participant in participant_records
                if participant.version_id == selected_version_id
            )
            participant_rank_values = []
            participant_score_pairs = {}
            participant_assigned_counts = Counter()
            for attempt in valid_attempts.values():
                session = session_by_id[attempt.session_id]
                if session.version_id == selected_version_id:
                    participant_assigned_counts[session.student_id] += 1
            for participant in selected_participants:
                score_pairs = [
                    (grade.score, grade.max_score)
                    for grade, _attempt in grades_by_student[participant.student_id]
                    if grade.score is not None and grade.max_score is not None
                ]
                participant_score_pairs[participant.student_id] = score_pairs
                maximum = sum(
                    (max_score for _score, max_score in score_pairs), Decimal("0")
                )
                comparable = (
                    participant_assigned_counts[participant.student_id] > 0
                    and len(score_pairs)
                    == participant_assigned_counts[participant.student_id]
                    and maximum > 0
                )
                participant_rank_values.append(
                    (
                        participant.student_id,
                        (
                            sum((score for score, _maximum in score_pairs), Decimal("0"))
                            / maximum
                            if comparable
                            else None
                        ),
                        participant.student.student_number
                        or participant.student.username,
                    )
                )
            participant_ranks = competition_ranks(participant_rank_values)
            for participant in selected_participants:
                student = participant.student
                evaluated = [
                    grade
                    for grade, _attempt in grades_by_student[student.pk]
                    if grade.is_correct is not None
                ]
                correct_count = sum(grade.is_correct is True for grade in evaluated)
                score_pairs = participant_score_pairs[student.pk]
                experiment_participants.append(
                    ExperimentParticipantRow(
                        generated_at=generated_at,
                        student_id=student.pk,
                        student_number=student.student_number or student.username,
                        display_name=student.shown_name,
                        is_current_member=student.pk in roster_ids,
                        participated=bool(session_versions_by_student[student.pk]),
                        completed=(
                            selected_version_id
                            in completed_versions_by_student[student.pk]
                        ),
                        correct_rate=_metric(
                            generated_at, correct_count, len(evaluated)
                        ),
                        score_rate=_metric(
                            generated_at,
                            sum(
                                (score for score, _maximum in score_pairs),
                                Decimal("0"),
                            ),
                            sum(
                                (maximum for _score, maximum in score_pairs),
                                Decimal("0"),
                            ),
                        ),
                        rank=participant_ranks.get(student.pk),
                        rank_sample_size=len(participant_ranks),
                    )
                )

        question_rows = []
        if experiment_id is not None and tasks:
            snapshots = QuestionSnapshot.objects.filter(
                version_id=tasks[0].release_snapshot_id
            ).order_by("source_sequence", "pk")
            for question in snapshots:
                question_grades = grades_by_snapshot[question.pk]
                question_submissions = [
                    (attempt, session_by_id[attempt.session_id])
                    for attempt in valid_attempts.values()
                    if attempt.id in submitted_attempt_ids
                    and attempt.snapshot_id == question.pk
                ]
                evaluated = [
                    (grade, attempt, session)
                    for grade, attempt, session in question_grades
                    if grade.is_correct is not None
                ]
                durations = [
                    Decimal(attempt.first_to_submit_duration_ms)
                    for attempt, _session in question_submissions
                    if attempt.first_to_submit_duration_ms is not None
                    and attempt.first_to_submit_duration_ms >= 0
                ]
                kcs = tuple(dict.fromkeys(question.all_kcs or [question.primary_kc]))
                correct_count = sum(
                    grade.is_correct is True for grade, _attempt, _session in evaluated
                )
                question_rows.append(
                    QuestionSummaryRow(
                        generated_at=generated_at,
                        question_snapshot_id=question.pk,
                        code=question.code,
                        text=question.text,
                        kcs=kcs,
                        answer_count=len(
                            {session.student_id for _attempt, session in question_submissions}
                        ),
                        correct_count=correct_count,
                        incorrect_count=len(evaluated) - correct_count,
                        correct_rate=_metric(
                            generated_at, correct_count, len(evaluated)
                        ),
                        average_answer_time_ms=_mean(durations),
                    )
                )

        return ClassCenterSnapshot(
            generated_at=generated_at,
            scope=ClassAnalysisScope(
                generated_at=generated_at,
                course_id=class_group.course_id,
                class_group_id=class_group.pk,
                experiment_id=experiment_id,
                task_count=len(tasks),
            ),
            has_live_experiments=any(
                task.experiment_status == "published" for task in tasks
            ),
            rank_sample_size=len(ranks),
            overview=ClassOverview(
                generated_at=generated_at,
                roster_count=len(roster),
                participated_count=len(participated_student_ids),
                correct_rate=_metric(
                    generated_at, total_correct, len(all_evaluated)
                ),
                completion_rate=_metric(
                    generated_at, completed_units, eligible_units
                ),
            ),
            class_average_correct_rate=class_average,
            correct_rate_distribution=tuple(
                DistributionBin(
                    generated_at=generated_at,
                    key=key,
                    label=label,
                    count=distribution_counts[key],
                )
                for key, label in _DISTRIBUTION_BINS
            ),
            participation=ParticipationBreakdown(
                generated_at=generated_at,
                completed=participation_counts["completed"],
                in_progress=participation_counts["in_progress"],
                not_participated=participation_counts["not_participated"],
            ),
            kc_rows=kc_rows,
            experiment_options=tuple(
                ExperimentOption(
                    generated_at=generated_at,
                    experiment_id=task.experiment_id,
                    name=task.experiment_name,
                    published_at=task.published_at,
                )
                for task in all_tasks
            ),
            experiment_rows=tuple(experiment_rows),
            experiment_participants=tuple(experiment_participants),
            students=StudentPage(
                generated_at=generated_at,
                items=student_rows,
                page=page,
                page_size=page_size,
                page_count=page_count,
                total_count=total_count,
                previous_page=page - 1 if page > 1 else None,
                next_page=page + 1 if page < page_count else None,
            ),
            question_rows=tuple(question_rows),
        )
