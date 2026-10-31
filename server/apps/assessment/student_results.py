from decimal import Decimal

from django.db.models import Prefetch

from apps.accounts.models import ClassStudentAnalyticsPolicy
from apps.experiments.models import Experiment
from apps.experiments.presentation import display_text, format_answer, options_for_snapshot

from .feedback_services import feedback_availability
from .models import Attempt, ExperimentSession, GradeRecord, SubmissionEvent


MINIMUM_COMPARISON_SAMPLE = 5


def _effective_grades(attempts):
    return {
        grade.submission.attempt_id: grade
        for grade in GradeRecord.objects.filter(
            submission__attempt_id__in=[attempt.pk for attempt in attempts],
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        ).select_related("submission")
    }


def _status_projection(session, attempts, submissions, grades):
    if session is None:
        return {
            "completion_status": "not_started",
            "submission_status": "not_started",
            "completed_at": None,
            "grading_status": "not_started",
        }
    submission_count = len(submissions)
    if submission_count == 0:
        submission_status = "not_submitted"
    elif submission_count == len(attempts):
        submission_status = "complete"
    else:
        submission_status = "partial"
    if submission_count == 0:
        grading_status = "not_started"
    elif len(grades) == submission_count:
        grading_status = "completed"
    elif grades:
        grading_status = "partial"
    else:
        grading_status = "pending"
    return {
        "completion_status": session.status,
        "submission_status": submission_status,
        "completed_at": session.completed_at,
        "grading_status": grading_status,
    }


def _score_summary(attempts, submissions, grades):
    if not attempts or len(submissions) != len(attempts) or len(grades) != len(attempts) or any(grade.score is None for grade in grades.values()):
        return None
    earned = sum((grade.score or Decimal("0") for grade in grades.values()), Decimal("0"))
    maximum = sum(
        (attempt.assignment.snapshot.max_score for attempt in attempts),
        Decimal("0"),
    )
    if maximum <= 0:
        return None
    return {
        "earned": earned,
        "maximum": maximum,
        "percent": (earned / maximum * Decimal("100")).quantize(Decimal("0.1")),
    }


def score_progress(attempts, submissions, grades):
    """Keep the paper denominator separate from the completed-grade denominator."""
    scored = [attempt for attempt in attempts if attempt.pk in grades and grades[attempt.pk].score is not None]
    scored_ids = {attempt.pk for attempt in scored}
    return {
        'earned': sum((grades[attempt.pk].score for attempt in scored), Decimal('0')),
        'paper_maximum': sum((attempt.assignment.snapshot.max_score for attempt in attempts), Decimal('0')),
        'graded_maximum': sum((attempt.assignment.snapshot.max_score for attempt in scored), Decimal('0')),
        'graded_count': len(scored), 'item_count': len(attempts),
        'pending_count': sum(attempt.pk in submissions and attempt.pk not in scored_ids for attempt in attempts),
        'unsubmitted_count': sum(attempt.pk not in submissions for attempt in attempts),
    }


def result_groups(attempts, items, submissions, grades, *, show_scores):
    """Add frozen shared content to already-authorized per-item result fields."""
    from apps.question_bank.services.question_groups import material_segments
    by_question = {item['question_id']: item for item in items}
    grouped = {}
    for attempt in attempts:
        snapshot = attempt.assignment.snapshot
        if snapshot.group_snapshot_id:
            grouped.setdefault(snapshot.group_snapshot_id, []).append(attempt)
    groups = []
    for group_attempts in grouped.values():
        group_attempts.sort(key=lambda attempt: attempt.assignment.snapshot.group_order)
        frozen = group_attempts[0].assignment.snapshot.group_snapshot
        members = []
        for attempt in group_attempts:
            question = attempt.assignment.snapshot
            member = {
                **by_question.get(question.pk, {}), 'item_key': str(question.pk), 'question_id': question.pk,
                'question_type': question.question_type, 'text': display_text(question.text),
                'options': [{'key': option.key, 'text': option.text} for option in options_for_snapshot(question)],
                'group_order': question.group_order, 'blank_id': question.blank_id,
            }
            if show_scores:
                member['max_score'] = question.max_score
            members.append(member)
        group = {'group_id': str(frozen.pk), 'source_group_id': frozen.source_group_id, 'kind': frozen.kind,
            'title': frozen.title, 'instructions': frozen.instructions, 'material': frozen.material,
            'content_language': frozen.content_language, 'members': members, 'item_count': len(members),
            'material_segments': material_segments(frozen, members)}
        if show_scores:
            group['score_progress'] = score_progress(group_attempts, submissions, grades)
        groups.append(group)
    return groups


def _correctness_summary(attempts, submissions, grades):
    if not attempts or len(submissions) != len(attempts) or len(grades) != len(attempts):
        return None
    values = [grade.is_correct for grade in grades.values()]
    if any(value is None for value in values):
        return None
    correct = sum(value is True for value in values)
    return {
        "correct": correct,
        "graded": len(values),
        "percent": (
            Decimal(correct) / Decimal(len(values)) * Decimal("100")
        ).quantize(Decimal("0.1")),
    }


def _session_score_ratio(session):
    attempts = list(
        getattr(session, "comparison_attempts", session.attempts.all())
    )
    if not attempts:
        return None
    if hasattr(session, "comparison_attempts"):
        submissions = {}
        grades = {}
        for attempt in attempts:
            try:
                submission = attempt.submission_event
            except SubmissionEvent.DoesNotExist:
                continue
            submissions[attempt.pk] = submission
            effective = next(
                iter(getattr(submission, "effective_grade_records", ())),
                None,
            )
            if effective is not None:
                grades[attempt.pk] = effective
    else:
        submissions = {
            submission.attempt_id: submission
            for submission in SubmissionEvent.objects.filter(
                attempt_id__in=[attempt.pk for attempt in attempts]
            )
        }
        grades = _effective_grades(attempts)
    summary = _score_summary(attempts, submissions, grades)
    if summary is None:
        return None
    return summary["earned"] / summary["maximum"]


def _comparison_projection(task, session, policy):
    from apps.analytics.student_comparisons import shared_results_enabled
    if not shared_results_enabled(task.experiment.course_id):
        return _legacy_comparison_projection(task, session, policy)
    branches = []
    if policy.show_class_average:
        branches.append("class_average")
    if policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF:
        branches.append("rank")
    if not branches or session is None or session.status != ExperimentSession.Status.COMPLETED:
        return None

    from apps.analytics.student_comparisons import read_comparisons, ready_payload
    payload = ready_payload(task, read_comparisons((task,), student_id=session.student_id))
    if payload is None or not payload.get("own_rank"):
        return None
    full = payload["full"]
    if full["sample_size"] < MINIMUM_COMPARISON_SAMPLE:
        return {branch: {"status": "unavailable", "reason": "minimum_sample"} for branch in branches}
    result = {}
    if "class_average" in branches:
        result["class_average"] = {"status": "available", "percent": (Decimal(full["mean"]) * Decimal("100")).quantize(Decimal("0.1")), "sample_size": full["sample_size"]}
    if "rank" in branches:
        result["rank"] = {"status": "available", **payload["own_rank"]}
    return result


def build_student_task_result(task, student):
    feedback_mode = task.release_snapshot.configuration.get(
        "feedback_mode", Experiment.Feedback.NONE
    )
    session = (
        ExperimentSession.objects.filter(
            class_task=task,
            participant_snapshot__student=student,
            student=student,
        )
        .prefetch_related(
            Prefetch(
                "attempts",
                queryset=Attempt.objects.select_related("assignment__snapshot__group_snapshot").order_by(
                    "assignment__assigned_sequence_index"
                ),
            )
        )
        .first()
    )
    attempts = list(session.attempts.all()) if session is not None else []
    submissions = {
        submission.attempt_id: submission
        for submission in SubmissionEvent.objects.filter(
            attempt_id__in=[attempt.pk for attempt in attempts]
        )
    }
    grades = _effective_grades(attempts)
    availability = feedback_availability(task, session)
    result = {
        "feedback_mode": feedback_mode,
        "task_id": task.pk,
        **_status_projection(session, attempts, submissions, grades),
    }
    if feedback_mode == Experiment.Feedback.NONE or not availability.released:
        return result

    policy = task.class_group.student_analytics_policy
    if policy.show_item_results:
        items = []
        for attempt in attempts:
            submission = submissions.get(attempt.pk)
            if submission is None:
                continue
            snapshot = attempt.assignment.snapshot
            options = options_for_snapshot(snapshot)
            grade = grades.get(attempt.pk)
            item_availability = feedback_availability(
                task,
                session,
                attempt=attempt,
                effective_grade=grade,
            )
            item = {
                'response_value': submission.final_answer,
                "question_id": snapshot.pk,
                "question_code": snapshot.code,
                "submitted_answer": format_answer(
                    snapshot.question_type,
                    submission.final_answer,
                    options,
                )
                or "（空）",
            }
            if policy.show_correct_rate and item_availability.allows_correctness:
                item["is_correct"] = grade.is_correct
            if policy.show_scores and item_availability.allows_correctness:
                item["score"] = grade.score
                item["max_score"] = snapshot.max_score
            if item_availability.allows_full:
                item["correct_answer"] = format_answer(
                    snapshot.question_type,
                    snapshot.correct_answer,
                    options,
                )
                item["explanation"] = display_text(snapshot.explanation)
            items.append(item)
        result["items"] = items
        if any(attempt.assignment.snapshot.group_snapshot_id for attempt in attempts):
            result['groups'] = result_groups(attempts, items, submissions, grades, show_scores=policy.show_scores)

    if policy.show_scores:
        result['score_progress'] = score_progress(attempts, submissions, grades)
        score = _score_summary(attempts, submissions, grades)
        if score is not None:
            result["score"] = score
    if policy.show_correct_rate:
        correctness = _correctness_summary(attempts, submissions, grades)
        if correctness is not None:
            result["correctness"] = correctness
    from apps.analytics.student_comparisons import shared_results_enabled
    if shared_results_enabled(task.experiment.course_id) and (policy.show_class_average or policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF):
        from apps.analytics.student_comparisons import read_comparisons, comparison_metadata
        result.update(comparison_metadata((task,), read_comparisons((task,), student_id=student.pk)))
    comparison = _comparison_projection(task, session, policy)
    if comparison is not None:
        result["comparison"] = comparison
    return result


def _legacy_comparison_projection(task, session, policy):
    branches = []
    if policy.show_class_average:
        branches.append("class_average")
    if policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF:
        branches.append("rank")
    if not branches or session is None or session.status != ExperimentSession.Status.COMPLETED:
        return None

    comparable = []
    sessions = (
        ExperimentSession.objects.filter(
            class_task=task,
            status=ExperimentSession.Status.COMPLETED,
        )
        .prefetch_related(
            Prefetch(
                "attempts",
                queryset=Attempt.objects.select_related(
                    "submission_event", "assignment__snapshot"
                ).prefetch_related(
                    Prefetch(
                        "submission_event__grade_records",
                        queryset=GradeRecord.objects.filter(
                            status=GradeRecord.Status.COMPLETED,
                            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
                        ),
                        to_attr="effective_grade_records",
                    )
                ),
                to_attr="comparison_attempts",
            )
        )
        .order_by("student_id")
    )
    for candidate in sessions:
        ratio = _session_score_ratio(candidate)
        if ratio is not None:
            comparable.append((candidate.student_id, ratio))
    own_ratio = next(
        (ratio for student_id, ratio in comparable if student_id == session.student_id),
        None,
    )
    if own_ratio is None:
        return None
    if len(comparable) < MINIMUM_COMPARISON_SAMPLE:
        return {
            branch: {"status": "unavailable", "reason": "minimum_sample"}
            for branch in branches
        }

    result = {}
    if "class_average" in branches:
        average = sum((ratio for _student_id, ratio in comparable), Decimal("0")) / Decimal(
            len(comparable)
        )
        result["class_average"] = {
            "status": "available",
            "percent": (average * Decimal("100")).quantize(Decimal("0.1")),
            "sample_size": len(comparable),
        }
    if "rank" in branches:
        higher = sum(ratio > own_ratio for _student_id, ratio in comparable)
        result["rank"] = {
            "status": "available",
            "position": higher + 1,
            "sample_size": len(comparable),
        }
    return result
