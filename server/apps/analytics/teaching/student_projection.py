from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from django.core.exceptions import PermissionDenied
from django.db.models import F
from django.urls import reverse

from apps.accounts.models import (
    ClassStudentAnalyticsPolicy,
    CourseEnrollment,
    User,
)
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    SubmissionEvent,
)
from apps.assessment.feedback_services import feedback_availability
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentVersionParticipantSnapshot,
    QuestionSnapshot,
)
from apps.experiments.presentation import (
    answer_option_values,
    display_text,
    format_answer,
    options_for_snapshot,
)

from .comparisons import competition_ranks
from .contracts import MetricValue
from .scope import analytics_read_transaction


MINIMUM_COMPARISON_SAMPLE = 5
_RESULT_FEEDBACK_MODES = frozenset(
    {Experiment.Feedback.CORRECTNESS, Experiment.Feedback.FULL}
)


class StudentAllowListDTO(Mapping):
    """Immutable mapping whose keys are exactly the fields a student may receive."""

    __slots__ = ("_values",)

    def __init__(self, **values):
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def to_dict(self):
        return {key: _plain(value) for key, value in self._values.items()}


def _plain(value):
    if isinstance(value, StudentAllowListDTO):
        return value.to_dict()
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


def student_projection_to_api(value):
    """Convert the student allow-list projection to JSON-safe public values."""
    if isinstance(value, StudentAllowListDTO):
        return {
            key: student_projection_to_api(item)
            for key, item in value.items()
        }
    if isinstance(value, MetricValue):
        return {
            "generated_at": value.generated_at,
            "numerator": value.numerator,
            "denominator": value.denominator,
            "ratio": value.ratio,
            "state": value.state,
            "sample_size": value.sample_size,
            "percent_display": value.percent_display,
            "percent_css": value.percent_css,
        }
    if isinstance(value, (tuple, list)):
        return [student_projection_to_api(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: student_projection_to_api(item)
            for key, item in value.items()
        }
    return value


@dataclass(frozen=True)
class StudentVisibilityContext:
    generated_at: object
    enrollment_id: int
    course_id: int
    class_group_id: int
    student_id: int
    policy_id: int


@dataclass(frozen=True)
class _StudentFactBundle:
    context: StudentVisibilityContext
    enrollment: CourseEnrollment
    policy: ClassStudentAnalyticsPolicy
    tasks: tuple
    feedback_by_task: Mapping
    feedback_availability_by_task: Mapping
    eligible_by_task: Mapping
    current_student_ids: frozenset
    student_by_id: Mapping
    session_by_task_student: Mapping
    attempts_by_task_student: Mapping
    submission_by_attempt: Mapping
    grade_by_attempt: Mapping
    comparisons: Mapping
    comparison_signatures: Mapping
    learning_comparison: object


def _metric(generated_at, numerator, denominator, *, sample_size=None):
    return MetricValue.fraction(
        generated_at=generated_at,
        numerator=numerator,
        denominator=denominator,
        sample_size=sample_size,
    )


def _load_bundle(enrollment, generated_at, *, experiment_id=None):
    """Background-only cohort fact loading."""
    return _load_fact_bundle(enrollment, generated_at, experiment_id=experiment_id, cohort=True)


def _load_http_bundle(enrollment, generated_at, *, experiment_id=None):
    from apps.analytics.student_comparisons import shared_results_enabled
    if shared_results_enabled(enrollment.course_id):
        return _load_personal_bundle(enrollment, generated_at, experiment_id=experiment_id)
    return _load_bundle(enrollment, generated_at, experiment_id=experiment_id)


def _load_personal_bundle(enrollment, generated_at, *, experiment_id=None):
    return _load_fact_bundle(enrollment, generated_at, experiment_id=experiment_id, cohort=False)


def _load_fact_bundle(enrollment, generated_at, *, experiment_id, cohort):
    try:
        current_enrollment = CourseEnrollment.objects.select_related(
            "course",
            "class_group__student_analytics_policy",
            "student",
        ).get(
            pk=enrollment.pk,
            student_id=enrollment.student_id,
            is_active=True,
        )
    except (CourseEnrollment.DoesNotExist, AttributeError) as error:
        raise LookupError("Active course enrollment is unavailable.") from error
    if current_enrollment.class_group.course_id != current_enrollment.course_id:
        raise LookupError("Enrollment class is outside the course.")
    if (
        current_enrollment.student.role != User.Role.STUDENT
        or not current_enrollment.student.is_active
    ):
        raise LookupError("Student account is unavailable.")

    class_group = current_enrollment.class_group
    policy = class_group.student_analytics_policy
    task_queryset = ClassExperimentTask.objects.filter(
            class_group=class_group,
            experiment__course_id=current_enrollment.course_id,
            experiment__status__in=(
                Experiment.Status.FROZEN,
                Experiment.Status.PUBLISHED,
                Experiment.Status.CLOSED,
            ),
            release_snapshot__experiment_id=F("experiment_id"),
            class_snapshot__version_id=F("release_snapshot_id"),
            class_snapshot__source_class_group_id=F("class_group_id"),
            published_at=F("release_snapshot__published_at"),
        )
    if experiment_id is not None:
        task_queryset = task_queryset.filter(experiment_id=experiment_id)
    if not cohort:
        task_queryset = task_queryset.filter(class_snapshot__participant_snapshots__student_id=current_enrollment.student_id)
    tasks = tuple(task_queryset.select_related("experiment", "release_snapshot").order_by("published_at", "pk"))
    task_by_pair = {
        (task.release_snapshot_id, task.class_snapshot_id): task for task in tasks
    }
    participant_queryset = ExperimentVersionParticipantSnapshot.objects.filter(
            version_id__in={task.release_snapshot_id for task in tasks},
            class_snapshot_id__in={task.class_snapshot_id for task in tasks},
            student__role=User.Role.STUDENT,
        )
    if not cohort:
        participant_queryset = participant_queryset.filter(student_id=current_enrollment.student_id)
    participants = tuple(participant for participant in participant_queryset.select_related("student")
        if (participant.version_id, participant.class_snapshot_id) in task_by_pair)
    eligible_by_task = {task.pk: set() for task in tasks}
    student_by_id = {}
    for participant in participants:
        task = task_by_pair[(participant.version_id, participant.class_snapshot_id)]
        eligible_by_task[task.pk].add(participant.student_id)
        student_by_id[participant.student_id] = participant.student

    current_student_ids = frozenset(
        CourseEnrollment.objects.filter(
            course_id=current_enrollment.course_id,
            class_group=class_group,
            is_active=True,
            student__is_active=True,
            student__role=User.Role.STUDENT,
        ).values_list("student_id", flat=True)
    ) if cohort else frozenset((current_enrollment.student_id,))
    eligible_student_ids = set().union(*eligible_by_task.values()) if tasks else set()
    sessions = tuple(
        session
        for session in ExperimentSession.objects.filter(
            version_id__in={task.release_snapshot_id for task in tasks},
            class_snapshot_id__in={task.class_snapshot_id for task in tasks},
            student_id__in=eligible_student_ids,
        ).select_related("student", "capture_contract")
        if (session.version_id, session.class_snapshot_id) in task_by_pair
        and session.student_id
        in eligible_by_task[
            task_by_pair[(session.version_id, session.class_snapshot_id)].pk
        ]
        and session.status != ExperimentSession.Status.INVALIDATED
    )
    session_by_id = {session.pk: session for session in sessions}
    session_by_task_student = {}
    for session in sessions:
        task = task_by_pair[(session.version_id, session.class_snapshot_id)]
        session_by_task_student[(task.pk, session.student_id)] = session

    attempts = tuple(
        attempt
        for attempt in Attempt.objects.filter(session_id__in=session_by_id).select_related(
            "assignment__snapshot"
        )
        if attempt.assignment.snapshot.version_id
        == session_by_id[attempt.session_id].version_id
    )
    attempts_by_task_student = {}
    for attempt in attempts:
        session = session_by_id[attempt.session_id]
        task = task_by_pair[(session.version_id, session.class_snapshot_id)]
        attempts_by_task_student.setdefault((task.pk, session.student_id), []).append(
            attempt
        )
    attempts_by_task_student = {
        key: tuple(
            sorted(value, key=lambda item: item.assignment.assigned_sequence_index)
        )
        for key, value in attempts_by_task_student.items()
    }
    attempt_by_id = {attempt.pk: attempt for attempt in attempts}
    submissions = tuple(
        SubmissionEvent.objects.filter(attempt_id__in=attempt_by_id)
    )
    submission_by_attempt = {
        submission.attempt_id: submission for submission in submissions
    }
    grades = tuple(
        GradeRecord.objects.filter(
            submission__attempt_id__in=attempt_by_id,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).select_related("submission")
    )
    grade_by_attempt = {grade.submission.attempt_id: grade for grade in grades}
    feedback_by_task = {
        task.pk: task.release_snapshot.configuration.get(
            "feedback_mode", Experiment.Feedback.NONE
        )
        for task in tasks
    }
    feedback_availability_by_task = {
        task.pk: feedback_availability(
            task,
            session_by_task_student.get(
                (task.pk, current_enrollment.student_id)
            ),
            at=generated_at,
        )
        for task in tasks
    }
    context = StudentVisibilityContext(
        generated_at=generated_at,
        enrollment_id=current_enrollment.pk,
        course_id=current_enrollment.course_id,
        class_group_id=class_group.pk,
        student_id=current_enrollment.student_id,
        policy_id=policy.pk,
    )
    from apps.analytics.student_comparisons import read_comparisons, read_learning_comparison, comparison_source_signatures
    comparisons = {} if cohort else read_comparisons(tasks, student_id=current_enrollment.student_id)
    comparison_signatures = {} if cohort else comparison_source_signatures(tasks)
    learning_comparison = None if cohort else read_learning_comparison(class_group.pk, current_enrollment.student_id)
    return _StudentFactBundle(
        context=context,
        enrollment=current_enrollment,
        policy=policy,
        tasks=tuple(
            task
            for task in tasks
            if current_enrollment.student_id in eligible_by_task[task.pk]
        ),
        feedback_by_task=MappingProxyType(feedback_by_task),
        feedback_availability_by_task=MappingProxyType(feedback_availability_by_task),
        eligible_by_task=MappingProxyType(
            {key: frozenset(value) for key, value in eligible_by_task.items()}
        ),
        current_student_ids=current_student_ids,
        student_by_id=MappingProxyType(student_by_id),
        session_by_task_student=MappingProxyType(session_by_task_student),
        attempts_by_task_student=MappingProxyType(attempts_by_task_student),
        submission_by_attempt=MappingProxyType(submission_by_attempt),
        grade_by_attempt=MappingProxyType(grade_by_attempt),
        comparisons=MappingProxyType(comparisons),
        comparison_signatures=MappingProxyType(comparison_signatures),
        learning_comparison=learning_comparison,
    )


def _feedback_allows_results(bundle, task):
    return (
        bundle.feedback_by_task[task.pk] in _RESULT_FEEDBACK_MODES
        and bundle.feedback_availability_by_task[task.pk].released
    )


def _task_attempts(bundle, task, student_id):
    return bundle.attempts_by_task_student.get((task.pk, student_id), ())


def _task_grades(bundle, task, student_id):
    return tuple(
        (bundle.grade_by_attempt[attempt.pk], attempt)
        for attempt in _task_attempts(bundle, task, student_id)
        if attempt.pk in bundle.grade_by_attempt
    )


def _correct_rate(bundle, task, student_id):
    evaluated = tuple(
        grade
        for grade, _attempt in _task_grades(bundle, task, student_id)
        if grade.is_correct is not None
    )
    return _metric(
        bundle.context.generated_at,
        sum(grade.is_correct is True for grade in evaluated),
        len(evaluated),
    )


def _score_rate(bundle, task, student_id):
    values = tuple(
        (grade.score, grade.max_score)
        for grade, _attempt in _task_grades(bundle, task, student_id)
        if grade.score is not None and grade.max_score is not None
    )
    return _metric(
        bundle.context.generated_at,
        sum((score for score, _maximum in values), Decimal("0")),
        sum((maximum for _score, maximum in values), Decimal("0")),
    )


def _grading_coverage(bundle, tasks, student_id):
    submitted_attempts = tuple(
        attempt
        for task in tasks
        for attempt in _task_attempts(bundle, task, student_id)
        if attempt.pk in bundle.submission_by_attempt
    )
    if not submitted_attempts:
        return None
    graded = sum(
        1
        for attempt in submitted_attempts
        if attempt.pk in bundle.grade_by_attempt
        and bundle.grade_by_attempt[attempt.pk].score is not None
        and bundle.grade_by_attempt[attempt.pk].max_score is not None
    )
    submitted = len(submitted_attempts)
    return StudentAllowListDTO(
        generated_at=bundle.context.generated_at,
        graded=graded,
        submitted=submitted,
        is_partial=graded < submitted,
    )


def _aggregate_metric(bundle, student_id, tasks, *, kind):
    grades = tuple(
        grade
        for task in tasks
        for grade, _attempt in _task_grades(bundle, task, student_id)
    )
    if kind == "correct":
        evaluated = tuple(grade for grade in grades if grade.is_correct is not None)
        return _metric(
            bundle.context.generated_at,
            sum(grade.is_correct is True for grade in evaluated),
            len(evaluated),
        )
    scored = tuple(
        (grade.score, grade.max_score)
        for grade in grades
        if grade.score is not None and grade.max_score is not None
    )
    return _metric(
        bundle.context.generated_at,
        sum((score for score, _maximum in scored), Decimal("0")),
        sum((maximum for _score, maximum in scored), Decimal("0")),
    )


def _macro_average(bundle, student_ids, tasks, *, kind):
    from apps.analytics.student_comparisons import shared_results_enabled
    if not shared_results_enabled(bundle.enrollment.course_id):
        return _legacy_macro_average(bundle, student_ids, tasks, kind=kind)
    from apps.analytics.student_comparisons import ready_payload, learning_payload
    if len(tasks) == 1:
        payload = ready_payload(tasks[0], bundle.comparisons, signatures=bundle.comparison_signatures)
    else:
        payload = learning_payload(tasks, bundle.learning_comparison, signatures=bundle.comparison_signatures)
    if payload is None:
        return None
    values = payload.get("means", {}).get(kind)
    if values is None or values[1] < MINIMUM_COMPARISON_SAMPLE:
        return None
    return _metric(bundle.context.generated_at, Decimal(values[0]), Decimal(values[1]), sample_size=values[1])


def _rank(bundle, task):
    from apps.analytics.student_comparisons import shared_results_enabled
    if not shared_results_enabled(bundle.enrollment.course_id):
        return _legacy_rank(bundle, task)
    from apps.analytics.student_comparisons import ready_payload
    payload = ready_payload(task, bundle.comparisons, signatures=bundle.comparison_signatures)
    rank = payload.get("own_rank") if payload else None
    if not rank or rank["sample_size"] < MINIMUM_COMPARISON_SAMPLE:
        return None
    return StudentAllowListDTO(generated_at=bundle.context.generated_at, **rank)


def _status(bundle, task):
    session = bundle.session_by_task_student.get(
        (task.pk, bundle.context.student_id)
    )
    if session is None:
        return "not_participated", "未参与", False, False
    labels = {
        ExperimentSession.Status.IN_PROGRESS: "进行中",
        ExperimentSession.Status.COMPLETED: "已完成",
        ExperimentSession.Status.EXPIRED: "已超时 / 未完成",
    }
    return (
        session.status,
        labels.get(session.status, "不可评估"),
        session.status == ExperimentSession.Status.COMPLETED,
        True,
    )


def _comparison(bundle, task, student_metric, *, kind):
    if not bundle.policy.show_class_average or student_metric.ratio is None:
        return None
    eligible_current = bundle.current_student_ids.intersection(
        bundle.eligible_by_task[task.pk]
    )
    class_average = _macro_average(
        bundle,
        eligible_current,
        (task,),
        kind=kind,
    )
    if class_average is None:
        return None
    return StudentAllowListDTO(
        generated_at=bundle.context.generated_at,
        student_value=student_metric,
        class_average=class_average,
    )


def _experiment_row(bundle, task):
    status, status_label, completed, participated = _status(bundle, task)
    values = {
        "generated_at": bundle.context.generated_at,
        "experiment_id": task.experiment_id,
        "name": task.experiment.name,
        "status": status,
        "status_label": status_label,
        "completed": completed,
        "participated": participated,
    }
    if not _feedback_allows_results(bundle, task):
        return StudentAllowListDTO(**values)
    values["results_available"] = True
    if bundle.policy.show_correct_rate:
        values["correct_rate"] = _correct_rate(
            bundle, task, bundle.context.student_id
        )
    if bundle.policy.show_scores:
        values["score_rate"] = _score_rate(
            bundle, task, bundle.context.student_id
        )
        coverage = _grading_coverage(
            bundle,
            (task,),
            bundle.context.student_id,
        )
        if coverage is not None:
            values["grading_coverage"] = coverage
    return StudentAllowListDTO(**values)


def _kc_rows(bundle, result_tasks):
    from apps.analytics.student_comparisons import shared_results_enabled
    if not shared_results_enabled(bundle.enrollment.course_id):
        return _legacy_kc_rows(bundle, result_tasks)
    if not (bundle.policy.show_correct_rate and bundle.policy.show_kc_rates):
        return None
    student_counts = {}
    for task in result_tasks:
        for grade, attempt in _task_grades(bundle, task, bundle.context.student_id):
            if grade.is_correct is None:
                continue
            for kc in dict.fromkeys(attempt.assignment.snapshot.all_kcs or [attempt.assignment.snapshot.primary_kc]):
                counter = student_counts.setdefault(kc, [0, 0])
                counter[0] += int(grade.is_correct is True)
                counter[1] += 1
    rows = []
    for name, (correct, total) in student_counts.items():
        values = {"generated_at": bundle.context.generated_at, "name": name, "student_value": _metric(bundle.context.generated_at, correct, total)}
        if bundle.policy.show_class_average:
            from apps.analytics.student_comparisons import learning_payload
            payload = learning_payload(result_tasks, bundle.learning_comparison, signatures=bundle.comparison_signatures)
            average = payload.get("kc_means", {}).get(name) if payload else None
            if average and average[1] >= MINIMUM_COMPARISON_SAMPLE:
                values["class_average"] = _metric(bundle.context.generated_at, Decimal(average[0]), Decimal(average[1]), sample_size=average[1])
        rows.append(StudentAllowListDTO(**values))
    return tuple(sorted(rows, key=lambda row: (row.student_value.ratio is None, row.student_value.ratio or Decimal("0"), row.name)))


def _trend_rows(bundle, result_tasks):
    if not (bundle.policy.show_correct_rate and bundle.policy.show_trend):
        return None
    raw_rows = []
    for task in result_tasks:
        student_value = _correct_rate(bundle, task, bundle.context.student_id)
        if student_value.ratio is None:
            continue
        values = {
            "generated_at": bundle.context.generated_at,
            "experiment_id": task.experiment_id,
            "name": task.experiment.name,
            "student_value": student_value,
        }
        comparison = _comparison(bundle, task, student_value, kind="correct")
        if comparison is not None:
            values["class_average"] = comparison.class_average
        raw_rows.append(values)
    rows = []
    count = len(raw_rows)
    for index, values in enumerate(raw_rows):
        values["x"] = (
            Decimal("50")
            if count < 2
            else Decimal("5")
            + Decimal("90") * Decimal(index) / Decimal(count - 1)
        )
        values["student_y"] = Decimal("95") - (
            values["student_value"].ratio * Decimal("90")
        )
        if "class_average" in values:
            values["class_y"] = Decimal("95") - (
                values["class_average"].ratio * Decimal("90")
            )
        rows.append(StudentAllowListDTO(**values))
    return tuple(rows)


def build_student_learning_projection(enrollment):
    with analytics_read_transaction() as generated_at:
        bundle = _load_http_bundle(enrollment, generated_at)
        result_tasks = tuple(
            task for task in bundle.tasks if _feedback_allows_results(bundle, task)
        )
        student_id = bundle.context.student_id
        completed_count = sum(_status(bundle, task)[2] for task in bundle.tasks)
        values = {
            "generated_at": generated_at,
            "course_id": bundle.enrollment.course_id,
            "course_name": bundle.enrollment.course.name,
            "course_code": bundle.enrollment.course.code,
            "course_term": bundle.enrollment.course.term,
            "class_group_name": bundle.enrollment.class_group.name,
            "completion": StudentAllowListDTO(
                generated_at=generated_at,
                completed=completed_count,
                eligible=len(bundle.tasks),
                rate=_metric(generated_at, completed_count, len(bundle.tasks)),
            ),
        }
        correct_rate = None
        score_rate = None
        if bundle.policy.show_correct_rate:
            correct_rate = _aggregate_metric(
                bundle, student_id, result_tasks, kind="correct"
            )
            values["correct_rate"] = correct_rate
        if bundle.policy.show_scores:
            score_rate = _aggregate_metric(
                bundle, student_id, result_tasks, kind="score"
            )
            values["score_rate"] = score_rate
            coverage = _grading_coverage(bundle, result_tasks, student_id)
            if coverage is not None:
                values["score_grading_coverage"] = coverage
        comparisons = {}
        from apps.analytics.student_comparisons import shared_results_enabled
        if bundle.policy.show_class_average and shared_results_enabled(bundle.enrollment.course_id):
            from apps.analytics.student_comparisons import learning_metadata
            values.update(learning_metadata(result_tasks, bundle.comparisons, bundle.learning_comparison, signatures=bundle.comparison_signatures))
        if bundle.policy.show_class_average:
            if correct_rate is not None and correct_rate.ratio is not None:
                class_value = _macro_average(
                    bundle,
                    bundle.current_student_ids,
                    result_tasks,
                    kind="correct",
                )
                if class_value is not None:
                    comparisons["correct_rate"] = StudentAllowListDTO(
                        generated_at=generated_at,
                        student_value=correct_rate,
                        class_average=class_value,
                    )
            if score_rate is not None and score_rate.ratio is not None:
                class_value = _macro_average(
                    bundle,
                    bundle.current_student_ids,
                    result_tasks,
                    kind="score",
                )
                if class_value is not None:
                    comparisons["score_rate"] = StudentAllowListDTO(
                        generated_at=generated_at,
                        student_value=score_rate,
                        class_average=class_value,
                    )
        if comparisons:
            values["comparisons"] = StudentAllowListDTO(**comparisons)
        experiment_rows = tuple(
            _experiment_row(bundle, task) for task in reversed(bundle.tasks)
        )
        values["experiments"] = experiment_rows
        if any("correct_rate" in row for row in experiment_rows):
            values["experiment_correct_rate_available"] = True
        if any("score_rate" in row for row in experiment_rows):
            values["experiment_score_rate_available"] = True
        kc_rows = _kc_rows(bundle, result_tasks)
        if kc_rows is not None:
            values["kc_rows"] = kc_rows
        trend_rows = _trend_rows(bundle, result_tasks)
        if trend_rows is not None:
            values["trend_rows"] = trend_rows
            if any("class_average" in row for row in trend_rows):
                values["trend_class_average_available"] = True
        return StudentAllowListDTO(**values)


def _resolve_task(bundle, experiment_id):
    task = next(
        (task for task in bundle.tasks if task.experiment_id == experiment_id),
        None,
    )
    if task is None:
        raise LookupError("Experiment is outside the student's frozen cohort.")
    return task


def _result_state(grade):
    if grade is None or grade.is_correct is None:
        return "pending"
    return "correct" if grade.is_correct else "incorrect"


def _question_options(question, student_answer, *, include_correct):
    student_values = set(
        answer_option_values(question.question_type, student_answer)
    )
    correct_values = set(
        answer_option_values(question.question_type, question.correct_answer)
    )
    rows = []
    for index, option in enumerate(options_for_snapshot(question)):
        if question.question_type == "true_false":
            value = "true" if index == 0 else "false"
            label = "T" if index == 0 else "F"
        else:
            value = option.key.strip().upper()
            label = option.key.strip()
        values = {
            "value": value,
            "label": label,
            "text": option.text,
            "selected_by_student": value in student_values,
        }
        if include_correct:
            values["is_correct"] = value in correct_values
        rows.append(StudentAllowListDTO(**values))
    return tuple(rows)


def _feedback_capture(*, task, session, attempt, availability):
    if (
        session.capture_contract_id is None
        or session.capture_contract.contract_name != "capture-v2"
    ):
        return None
    return StudentAllowListDTO(
        capture_version="capture-v2",
        session_id=session.pk,
        attempt_id=attempt.pk,
        event_batch_url=reverse("assessment_process:capture_v2_batch"),
        checkpoint_url=reverse("assessment_process:capture_checkpoint"),
        time_sync_create_url=reverse("assessment_process:time_sync_create"),
        time_sync_finalize_url=reverse("assessment_process:time_sync_finalize"),
        app_version="2026.08.29.2",
        heartbeat_interval_seconds=task.release_snapshot.configuration.get(
            "heartbeat_interval_seconds", 30
        ),
        answer_event_id=(
            str(attempt.final_response_event_id)
            if attempt.final_response_event_id is not None
            else None
        ),
        snapshot_id=(
            str(attempt.final_response_snapshot_id)
            if attempt.final_response_snapshot_id is not None
            else None
        ),
        mode=availability.feedback_mode,
        authorized=StudentAllowListDTO(
            correctness=availability.allows_correctness,
            correct_answer=availability.allows_full,
            explanation=availability.allows_full,
        ),
    )


def _item_rows(bundle, task):
    rows = []
    session = bundle.session_by_task_student.get(
        (task.pk, bundle.context.student_id)
    )
    for attempt in _task_attempts(bundle, task, bundle.context.student_id):
        submission = bundle.submission_by_attempt.get(attempt.pk)
        if submission is None:
            continue
        question = attempt.assignment.snapshot
        grade = bundle.grade_by_attempt.get(attempt.pk)
        availability = feedback_availability(
            task,
            session,
            attempt=attempt,
            effective_grade=grade,
            at=bundle.context.generated_at,
        )
        result_label = "待教师批改"
        if grade is not None and grade.is_correct is not None:
            result_label = "正确" if grade.is_correct else "错误"
        values = {
            "generated_at": bundle.context.generated_at,
            "question_id": question.pk,
            "code": question.code,
            "kcs": tuple(dict.fromkeys(question.all_kcs or [question.primary_kc])),
            "result_label": result_label,
            "result_state": _result_state(grade),
            "final_answer": format_answer(
                question.question_type,
                submission.final_answer,
                options_for_snapshot(question),
            )
            or "（空）",
        }
        if bundle.policy.show_scores and availability.allows_correctness:
            values["score"] = StudentAllowListDTO(
                generated_at=bundle.context.generated_at,
                value=grade.score,
                maximum=grade.max_score,
            )
        rows.append(StudentAllowListDTO(**values))
    return tuple(rows)


def build_student_experiment_projection(
    enrollment,
    *,
    experiment_id,
    require_results=False,
):
    with analytics_read_transaction() as generated_at:
        return _build_student_experiment_projection(
            enrollment, experiment_id=experiment_id,
            require_results=require_results, generated_at=generated_at,
        )


def _build_student_experiment_projection(
    enrollment, *, experiment_id, require_results, generated_at,
):
    bundle = _load_http_bundle(enrollment, generated_at, experiment_id=experiment_id)
    task = _resolve_task(bundle, experiment_id)
    result_allowed = _feedback_allows_results(bundle, task)
    if require_results and not result_allowed:
        raise PermissionDenied("该实验未开放结果反馈。")
    status, status_label, completed, participated = _status(bundle, task)
    values = {
        "generated_at": generated_at,
        "course_id": bundle.enrollment.course_id,
        "course_name": bundle.enrollment.course.name,
        "class_group_name": bundle.enrollment.class_group.name,
        "experiment_id": task.experiment_id,
        "experiment_name": task.experiment.name,
        "status": status,
        "status_label": status_label,
        "completed": completed,
        "participated": participated,
    }
    if not result_allowed:
        return StudentAllowListDTO(**values)
    from apps.analytics.student_comparisons import shared_results_enabled
    if shared_results_enabled(bundle.enrollment.course_id) and (bundle.policy.show_class_average or bundle.policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF):
        from apps.analytics.student_comparisons import comparison_metadata
        values.update(comparison_metadata((task,), bundle.comparisons, signatures=bundle.comparison_signatures))
    if bundle.policy.show_correct_rate:
        metric = _correct_rate(bundle, task, bundle.context.student_id)
        values["correct_rate"] = metric
        comparison = _comparison(bundle, task, metric, kind="correct")
        if comparison is not None:
            values["correct_rate_comparison"] = comparison
    if bundle.policy.show_scores:
        metric = _score_rate(bundle, task, bundle.context.student_id)
        values["score_rate"] = metric
        coverage = _grading_coverage(
            bundle,
            (task,),
            bundle.context.student_id,
        )
        if coverage is not None:
            values["grading_coverage"] = coverage
        comparison = _comparison(bundle, task, metric, kind="score")
        if comparison is not None:
            values["score_rate_comparison"] = comparison
        if bundle.policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF:
            rank = _rank(bundle, task)
            if rank is not None:
                values["rank"] = rank
    if bundle.policy.show_item_results:
        values["item_results"] = _item_rows(bundle, task)
    return StudentAllowListDTO(**values)


def build_student_question_projection(
    enrollment,
    *,
    experiment_id,
    question_id,
):
    with analytics_read_transaction() as generated_at:
        bundle = _load_http_bundle(enrollment, generated_at, experiment_id=experiment_id)
        task = _resolve_task(bundle, experiment_id)
        try:
            question = QuestionSnapshot.objects.get(
                pk=question_id,
                version_id=task.release_snapshot_id,
            )
        except QuestionSnapshot.DoesNotExist as error:
            raise LookupError("Question is outside the student's experiment.") from error
        if not _feedback_allows_results(bundle, task):
            raise PermissionDenied("该实验未开放结果反馈。")
        if not bundle.policy.show_item_results:
            raise PermissionDenied("教师未开放逐题结果。")
        attempt = next(
            (
                item
                for item in _task_attempts(bundle, task, bundle.context.student_id)
                if item.assignment.snapshot_id == question.pk
            ),
            None,
        )
        if attempt is None or attempt.pk not in bundle.submission_by_attempt:
            raise LookupError("Student has no formal submission for this question.")
        submission = bundle.submission_by_attempt[attempt.pk]
        grade = bundle.grade_by_attempt.get(attempt.pk)
        availability = feedback_availability(
            task,
            bundle.session_by_task_student.get(
                (task.pk, bundle.context.student_id)
            ),
            attempt=attempt,
            effective_grade=grade,
            at=generated_at,
        )
        result_label = "待教师批改"
        if grade is not None and grade.is_correct is not None:
            result_label = "正确" if grade.is_correct else "错误"
        options = options_for_snapshot(question)
        include_correct = availability.allows_full
        values = {
            "generated_at": generated_at,
            "course_id": bundle.enrollment.course_id,
            "course_name": bundle.enrollment.course.name,
            "class_group_name": bundle.enrollment.class_group.name,
            "experiment_id": task.experiment_id,
            "experiment_name": task.experiment.name,
            "question_id": question.pk,
            "code": question.code,
            "question_type": question.question_type,
            "text": display_text(question.text),
            "kcs": tuple(dict.fromkeys(question.all_kcs or [question.primary_kc])),
            "result_label": result_label,
            "result_state": _result_state(grade),
            "final_answer": format_answer(
                question.question_type,
                submission.final_answer,
                options,
            )
            or "（空）",
            "options": _question_options(
                question,
                submission.final_answer,
                include_correct=include_correct,
            ),
        }
        feedback_capture = _feedback_capture(
            task=task,
            session=bundle.session_by_task_student[(
                task.pk,
                bundle.context.student_id,
            )],
            attempt=attempt,
            availability=availability,
        )
        if feedback_capture is not None:
            values["feedback_capture"] = feedback_capture
        if bundle.policy.show_scores and availability.allows_correctness:
            values["score"] = StudentAllowListDTO(
                generated_at=generated_at,
                value=grade.score,
                maximum=grade.max_score,
            )
        if include_correct:
            values["correct_answer"] = format_answer(
                question.question_type,
                question.correct_answer,
                options,
            )
            values["explanation"] = display_text(question.explanation)
        return StudentAllowListDTO(**values)


def _legacy_macro_average(bundle, student_ids, tasks, *, kind):
    metrics = tuple(
        _aggregate_metric(bundle, student_id, tasks, kind=kind)
        for student_id in student_ids
    )
    ratios = tuple(metric.ratio for metric in metrics if metric.ratio is not None)
    if len(ratios) < MINIMUM_COMPARISON_SAMPLE:
        return None
    return _metric(
        bundle.context.generated_at,
        sum(ratios, Decimal("0")),
        Decimal(len(ratios)),
        sample_size=len(ratios),
    )


def _legacy_rank(bundle, task):
    values = []
    for student_id in bundle.eligible_by_task[task.pk]:
        session = bundle.session_by_task_student.get((task.pk, student_id))
        attempts = _task_attempts(bundle, task, student_id)
        grades = _task_grades(bundle, task, student_id)
        scored = tuple(
            (grade.score, grade.max_score)
            for grade, _attempt in grades
            if grade.score is not None and grade.max_score is not None
        )
        total = sum((score for score, _maximum in scored), Decimal("0"))
        maximum = sum((maximum for _score, maximum in scored), Decimal("0"))
        comparable = (
            session is not None
            and session.status == ExperimentSession.Status.COMPLETED
            and bool(attempts)
            and all(attempt.pk in bundle.submission_by_attempt for attempt in attempts)
            and len(scored) == len(attempts)
            and maximum > 0
        )
        student = bundle.student_by_id.get(student_id)
        stable_label = (
            student.student_number or student.username
            if student is not None
            else str(student_id)
        )
        values.append(
            (student_id, total / maximum if comparable else None, stable_label)
        )
    ranks = competition_ranks(values)
    if len(ranks) < MINIMUM_COMPARISON_SAMPLE:
        return None
    position = ranks.get(bundle.context.student_id)
    if position is None:
        return None
    return StudentAllowListDTO(
        generated_at=bundle.context.generated_at,
        position=position,
        sample_size=len(ranks),
    )


def _legacy_kc_rows(bundle, result_tasks):
    if not (bundle.policy.show_correct_rate and bundle.policy.show_kc_rates):
        return None
    student_id = bundle.context.student_id
    student_counts = {}
    class_counts = {}
    for task in result_tasks:
        for current_id in bundle.current_student_ids.intersection(
            bundle.eligible_by_task[task.pk]
        ):
            for grade, attempt in _task_grades(bundle, task, current_id):
                if grade.is_correct is None:
                    continue
                kcs = tuple(
                    dict.fromkeys(
                        attempt.assignment.snapshot.all_kcs
                        or [attempt.assignment.snapshot.primary_kc]
                    )
                )
                for kc in kcs:
                    values = class_counts.setdefault(kc, {}).setdefault(
                        current_id, [0, 0]
                    )
                    values[0] += int(grade.is_correct is True)
                    values[1] += 1
                    if current_id == student_id:
                        own = student_counts.setdefault(kc, [0, 0])
                        own[0] += int(grade.is_correct is True)
                        own[1] += 1
    rows = []
    for name, (correct, total) in student_counts.items():
        values = {
            "generated_at": bundle.context.generated_at,
            "name": name,
            "student_value": _metric(
                bundle.context.generated_at, correct, total
            ),
        }
        ratios = tuple(
            Decimal(counts[0]) / Decimal(counts[1])
            for counts in class_counts.get(name, {}).values()
            if counts[1]
        )
        if bundle.policy.show_class_average and len(ratios) >= MINIMUM_COMPARISON_SAMPLE:
            values["class_average"] = _metric(
                bundle.context.generated_at,
                sum(ratios, Decimal("0")),
                Decimal(len(ratios)),
                sample_size=len(ratios),
            )
        rows.append(StudentAllowListDTO(**values))
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.student_value.ratio is None,
                row.student_value.ratio or Decimal("0"),
                row.name,
            ),
        )
    )
