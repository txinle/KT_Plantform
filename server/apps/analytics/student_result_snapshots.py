from __future__ import annotations

import hashlib
import json
import logging

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.db.models import Count, F, Max
from rest_framework.renderers import JSONRenderer

from apps.accounts.models import CourseEnrollment, User
from apps.assessment.feedback_services import feedback_availability
from apps.assessment.models import ExperimentSession, GradeRecord, SubmissionEvent
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentVersionParticipantSnapshot,
)

from .models import StudentExperimentResultSnapshot
from .teaching.scope import analytics_read_transaction


PROJECTION_VERSION = "student-result-v2"
logger = logging.getLogger(__name__)


def _current_enrollment(enrollment):
    try:
        return (
            CourseEnrollment.objects.select_related(
                "course",
                "class_group__student_analytics_policy",
                "student",
            )
            .get(
                pk=enrollment.pk,
                student_id=enrollment.student_id,
                is_active=True,
            )
        )
    except CourseEnrollment.DoesNotExist as error:
        raise LookupError("Active course enrollment is unavailable.") from error


def _authorize_result_access(enrollment, experiment_id, *, at):
    current = _current_enrollment(enrollment)
    if current.class_group.course_id != current.course_id:
        raise LookupError("Enrollment class is outside the course.")
    if current.student.role != User.Role.STUDENT or not current.student.is_active:
        raise LookupError("Student account is unavailable.")
    task = (
        ClassExperimentTask.objects.select_related(
            "experiment",
            "release_snapshot",
            "class_snapshot",
        )
        .filter(
            class_group_id=current.class_group_id,
            experiment_id=experiment_id,
            experiment__course_id=current.course_id,
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
        .first()
    )
    if task is None:
        raise LookupError("Experiment is outside the student's frozen cohort.")
    if not ExperimentVersionParticipantSnapshot.objects.filter(
        version_id=task.release_snapshot_id,
        class_snapshot_id=task.class_snapshot_id,
        student_id=current.student_id,
    ).exists():
        raise LookupError("Experiment is outside the student's frozen cohort.")
    session = (
        ExperimentSession.objects.filter(
            version_id=task.release_snapshot_id,
            class_snapshot_id=task.class_snapshot_id,
            student_id=current.student_id,
        )
        .exclude(status=ExperimentSession.Status.INVALIDATED)
        .order_by("-pk")
        .first()
    )
    availability = feedback_availability(task, session, at=at)
    if not availability.released or availability.feedback_mode not in {
        Experiment.Feedback.CORRECTNESS,
        Experiment.Feedback.FULL,
    }:
        raise PermissionDenied("该实验未开放结果反馈。")
    policy = current.class_group.student_analytics_policy
    if policy is None:
        raise LookupError("Student analytics policy is unavailable.")
    return current, task, session, policy


def _iso(value):
    return value.isoformat() if value is not None else None


def _source_signature(*, enrollment, task, session, policy):
    from .student_comparisons import shared_results_enabled
    if not shared_results_enabled(enrollment.course_id):
        return _legacy_source_signature(enrollment=enrollment, task=task, session=session, policy=policy)
    session_revision = session.result_revision if session is not None else 0
    source = {
        "projection_version": PROJECTION_VERSION,
        "course_id": enrollment.course_id,
        "course_name": enrollment.course.name,
        "class_name": enrollment.class_group.name,
        "experiment_name": task.experiment.name,
        "class_task_id": task.pk,
        "student_id": enrollment.student_id,
        "release_snapshot_id": task.release_snapshot_id,
        "release_definition": task.release_snapshot.definition_fingerprint,
        "release_configuration": task.release_snapshot.configuration,
        "session_revision": session_revision,
        "session_status": session.status if session is not None else None,
        "session_terminal_at": _iso(session.terminal_at) if session is not None else None,
        "feedback_released_at": _iso(task.feedback_released_at),
        "analytics_policy_id": policy.pk,
        "analytics_policy_updated_at": _iso(policy.updated_at),
        "analytics_policy": {
            "show_scores": policy.show_scores,
            "show_correct_rate": policy.show_correct_rate,
            "show_item_results": policy.show_item_results,
            "show_class_average": policy.show_class_average,
            "rank_mode": policy.rank_mode,
            "show_kc_rates": policy.show_kc_rates,
            "show_trend": policy.show_trend,
        },
    }
    return hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _serialize_projection(projection):
    from apps.assessment.api.serializers import StudentLearningExperimentSerializer
    from .teaching.student_projection import student_projection_to_api

    serialized = StudentLearningExperimentSerializer(
        instance=student_projection_to_api(projection)
    ).data
    return json.loads(JSONRenderer().render(serialized))


def get_student_experiment_result_payload(enrollment, *, experiment_id):
    """Authorize first, then serve a matching result snapshot or refresh it."""
    from .teaching.student_projection import _build_student_experiment_projection

    # Authorization, dependency signature and payload share one read snapshot.
    # Cache persistence follows outside the PostgreSQL read-only transaction.
    with analytics_read_transaction() as generated_at:
        current, task, session, policy = _authorize_result_access(
            enrollment, experiment_id, at=generated_at,
        )
        signature = _source_signature(
            enrollment=current, task=task, session=session, policy=policy,
        )
        try:
            with transaction.atomic():
                cached = StudentExperimentResultSnapshot.objects.filter(
                    class_task_id=task.pk, student_id=current.student_id,
                    source_signature=signature, projection_version=PROJECTION_VERSION,
                ).first()
        except DatabaseError:
            logger.exception("Student result snapshot read failed; using live projection")
            cached = None
        if cached is not None:
            return _with_comparisons(cached.payload, current, task, session, policy, generated_at)
        projection = _build_student_experiment_projection(
            current, experiment_id=experiment_id, require_results=True,
            generated_at=generated_at,
        )
        payload = _serialize_projection(projection)
        from .student_comparisons import shared_results_enabled
        if shared_results_enabled(current.course_id):
            for key in ("comparison_status", "comparison_generated_at", "correct_rate_comparison", "score_rate_comparison", "rank"):
                payload.pop(key, None)
        # Keep comparison versions, their content and access in this snapshot.
        response_payload = _with_comparisons(payload, current, task, session, policy, generated_at)
    try:
        with transaction.atomic():
            StudentExperimentResultSnapshot.objects.update_or_create(
                class_task_id=task.pk,
                student_id=current.student_id,
                defaults={
                    "source_signature": signature,
                    "projection_version": PROJECTION_VERSION,
                    "payload": payload,
                    "generated_at": projection.generated_at,
                },
            )
    except (DatabaseError, TypeError, ValueError):
        logger.exception("Student result snapshot write failed; returning live projection")
    return response_payload


def _with_comparisons(payload, enrollment, task, session, policy, generated_at):
    from apps.accounts.models import ClassStudentAnalyticsPolicy
    from .student_comparisons import read_comparisons, comparison_metadata, ready_payload, comparison_source_signatures
    from .teaching.student_projection import _metric, student_projection_to_api
    result = dict(payload)
    from .student_comparisons import shared_results_enabled
    if not shared_results_enabled(enrollment.course_id):
        return result
    if not policy.show_class_average and policy.rank_mode != ClassStudentAnalyticsPolicy.RankMode.SELF:
        return result
    rows = read_comparisons((task,), student_id=enrollment.student_id)
    signatures = comparison_source_signatures((task,))
    result.update(json.loads(JSONRenderer().render(comparison_metadata((task,), rows, signatures=signatures))))
    summary = ready_payload(task, rows, signatures=signatures)
    if summary is None:
        return result
    if policy.show_class_average:
        for kind, metric_key in (("correct", "correct_rate"), ("score", "score_rate")):
            metric = result.get(metric_key)
            values = summary.get("means", {}).get(kind)
            if metric is None or metric.get("ratio") is None or values is None or values[1] < 5:
                continue
            from decimal import Decimal
            average = _metric(rows[task.pk].generated_at, Decimal(values[0]), Decimal(values[1]), sample_size=values[1])
            result[metric_key + "_comparison"] = {"generated_at": payload["generated_at"], "student_value": metric, "class_average": json.loads(JSONRenderer().render(student_projection_to_api(average)))}
    rank = summary.get("own_rank")
    if policy.show_scores and policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF and rank and rank["sample_size"] >= 5:
        result["rank"] = {"generated_at": payload["generated_at"], **rank}
    return result


def _legacy_source_signature(*, enrollment, task, session, policy):
    session_qs = ExperimentSession.objects.filter(
        version_id=task.release_snapshot_id,
        class_snapshot_id=task.class_snapshot_id,
    ).exclude(status=ExperimentSession.Status.INVALIDATED)
    session_revision = session.research_source_revision if session is not None else 0
    related_revisions = list(session_qs.order_by("pk").values_list(
        "pk", "student_id", "research_source_revision", "status",
    ))
    current_student_ids = list(CourseEnrollment.objects.filter(
        course_id=enrollment.course_id, class_group_id=enrollment.class_group_id,
        is_active=True, student__is_active=True, student__role=User.Role.STUDENT,
    ).order_by("student_id").values_list("student_id", flat=True))
    participants = list(ExperimentVersionParticipantSnapshot.objects.filter(
        version_id=task.release_snapshot_id, class_snapshot_id=task.class_snapshot_id,
        student__role=User.Role.STUDENT,
    ).order_by("student_id").values_list("student_id", "student__student_number", "student__username"))
    grade_revision = GradeRecord.objects.filter(
        submission__attempt__session__version_id=task.release_snapshot_id,
        submission__attempt__session__class_snapshot_id=task.class_snapshot_id,
    ).aggregate(
        count=Count("pk"),
        latest_created_at=Max("created_at"),
        latest_graded_at=Max("graded_at"),
        latest_voided_at=Max("voided_at"),
    )
    submission_revision = SubmissionEvent.objects.filter(
        attempt__session__version_id=task.release_snapshot_id,
        attempt__session__class_snapshot_id=task.class_snapshot_id,
    ).aggregate(
        count=Count("pk"),
        latest_committed_at=Max("committed_at"),
    )
    source = {
        "projection_version": PROJECTION_VERSION,
        "course_id": enrollment.course_id,
        "course_name": enrollment.course.name,
        "class_name": enrollment.class_group.name,
        "experiment_name": task.experiment.name,
        "class_task_id": task.pk,
        "student_id": enrollment.student_id,
        "release_snapshot_id": task.release_snapshot_id,
        "release_definition": task.release_snapshot.definition_fingerprint,
        "release_configuration": task.release_snapshot.configuration,
        "session_revision": session_revision,
        "session_status": session.status if session is not None else None,
        "session_terminal_at": _iso(session.terminal_at) if session is not None else None,
        "related_session_revisions": related_revisions,
        "current_student_ids": current_student_ids,
        "participants": participants,
        "grade_count": grade_revision["count"],
        "latest_grade_created_at": _iso(grade_revision["latest_created_at"]),
        "latest_graded_at": _iso(grade_revision["latest_graded_at"]),
        "latest_voided_at": _iso(grade_revision["latest_voided_at"]),
        "submission_count": submission_revision["count"],
        "latest_submission_at": _iso(submission_revision["latest_committed_at"]),
        "feedback_released_at": _iso(task.feedback_released_at),
        "analytics_policy_id": policy.pk,
        "analytics_policy_updated_at": _iso(policy.updated_at),
        "analytics_policy": {
            "show_scores": policy.show_scores,
            "show_correct_rate": policy.show_correct_rate,
            "show_item_results": policy.show_item_results,
            "show_class_average": policy.show_class_average,
            "rank_mode": policy.rank_mode,
            "show_kc_rates": policy.show_kc_rates,
            "show_trend": policy.show_trend,
        },
    }
    return hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
