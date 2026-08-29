import json
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from apps.accounts.permissions import role_required
from apps.accounts.models import Course, CourseEnrollment
from apps.experiments.models import Experiment
from .models import (
    Attempt,
    ConfidenceAssignment,
    ExperimentSession,
    StudentQuestionAssignment,
)
from .confidence_services import (
    confidence_prompt_contract,
    ensure_confidence_assignment,
    frozen_feedback_payload,
)
from .services import (
    SubmissionValidationError,
    finish_session,
    get_or_create_session,
    mark_skipped,
    record_submission_rejection,
    record_visit,
    save_draft,
    submit_attempt,
)


def _client_time(value):
    return parse_datetime(value) if value else None


def _json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


def _next_assignment(session, current_id=None):
    candidates = list(session.assignments.select_related("attempt").order_by("assigned_sequence_index"))
    current = next((item for item in candidates if item.id == current_id), None)
    if current:
        after = [item for item in candidates if item.assigned_sequence_index > current.assigned_sequence_index]
        before = [item for item in candidates if item.assigned_sequence_index <= current.assigned_sequence_index]
        candidates = after + before
    for item in candidates:
        if item.id != current_id and item.attempt.status not in {Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED}:
            return item
    return None


def _next_url_after_attempt(attempt, *, finish_if_last):
    next_assignment = _next_assignment(attempt.session, attempt.assignment_id)
    if next_assignment:
        return reverse(
            "assessment:question",
            args=[attempt.session_id, next_assignment.id],
        )
    if finish_if_last:
        finish_session(attempt.session_id)
    return reverse("assessment:complete", args=[attempt.session_id])


def _confidence_client_contract(attempt, confidence):
    return {
        **confidence_prompt_contract(confidence),
        "prompt_shown_url": reverse(
            "assessment_process:confidence_prompt_shown",
            args=[attempt.pk],
        ),
        "commit_url": reverse(
            "assessment_process:confidence_commit",
            args=[attempt.pk],
        ),
        "skip_url": reverse(
            "assessment_process:confidence_skip",
            args=[attempt.pk],
        ),
    }


@role_required("student")
def dashboard(request):
    enrollments = CourseEnrollment.objects.filter(
        student=request.user,
        is_active=True,
    ).select_related("course", "class_group")
    return render(request, "student/dashboard.html", {"course_enrollments": enrollments})


@role_required("student")
def course_dashboard(request, course_id):
    enrollment = get_object_or_404(
        CourseEnrollment.objects.select_related("course", "class_group"),
        course_id=course_id,
        student=request.user,
        is_active=True,
    )
    experiments = list(
        Experiment.objects.filter(
            course=enrollment.course,
            student_links__student=request.user,
        ).distinct()
    )
    sessions = {
        session.version.experiment_id: session
        for session in request.user.assessment_sessions.select_related("version__experiment")
        if session.version.experiment.course_id == enrollment.course_id
    }
    cards = [{"experiment": experiment, "session": sessions.get(experiment.id)} for experiment in experiments]
    return render(
        request,
        "student/course.html",
        {
            "enrollment": enrollment,
            "course": enrollment.course,
            "experiment_cards": cards,
            "student_learning_enabled": settings.STUDENT_LEARNING_ANALYTICS_ENABLED,
            "student_course_section": "experiments",
        },
    )


def _student_learning_enrollment(request, course_id):
    if not settings.STUDENT_LEARNING_ANALYTICS_ENABLED:
        raise Http404
    return get_object_or_404(
        CourseEnrollment.objects.select_related("course", "class_group"),
        course_id=course_id,
        student=request.user,
        is_active=True,
    )


def _student_learning_forbidden(request, enrollment, message):
    return render(
        request,
        "student/learning_forbidden.html",
        {
            "course": enrollment.course,
            "enrollment": enrollment,
            "message": message,
            "student_learning_enabled": True,
            "student_course_section": "learning",
        },
        status=403,
    )


@require_GET
@role_required("student")
def student_learning(request, course_id):
    enrollment = _student_learning_enrollment(request, course_id)
    from apps.analytics.teaching.student_projection import (
        build_student_learning_projection,
    )

    try:
        learning = build_student_learning_projection(enrollment)
    except LookupError as error:
        raise Http404 from error
    return render(
        request,
        "student/learning.html",
        {
            "course": enrollment.course,
            "enrollment": enrollment,
            "learning": learning,
            "student_learning_enabled": True,
            "student_course_section": "learning",
        },
    )


@require_GET
@role_required("student")
def student_learning_experiment(request, course_id, experiment_id):
    enrollment = _student_learning_enrollment(request, course_id)
    from apps.analytics.teaching.student_projection import (
        build_student_experiment_projection,
    )

    try:
        detail = build_student_experiment_projection(
            enrollment,
            experiment_id=experiment_id,
            require_results=True,
        )
    except PermissionDenied as error:
        return _student_learning_forbidden(request, enrollment, str(error))
    except LookupError as error:
        raise Http404 from error
    return render(
        request,
        "student/learning_experiment.html",
        {
            "course": enrollment.course,
            "enrollment": enrollment,
            "detail": detail,
            "student_learning_enabled": True,
            "student_course_section": "learning",
        },
    )


@require_GET
@role_required("student")
def student_learning_question(request, course_id, experiment_id, question_id):
    enrollment = _student_learning_enrollment(request, course_id)
    from apps.analytics.teaching.student_projection import (
        build_student_question_projection,
    )

    try:
        detail = build_student_question_projection(
            enrollment,
            experiment_id=experiment_id,
            question_id=question_id,
        )
    except PermissionDenied as error:
        return _student_learning_forbidden(request, enrollment, str(error))
    except LookupError as error:
        raise Http404 from error
    return render(
        request,
        "student/learning_question.html",
        {
            "course": enrollment.course,
            "enrollment": enrollment,
            "detail": detail,
            "student_learning_enabled": True,
            "student_course_section": "learning",
        },
    )


@role_required("student")
def start_experiment(request, experiment_id):
    experiment = get_object_or_404(Experiment.objects.select_related("course"), pk=experiment_id)
    try:
        session = get_or_create_session(experiment, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("assessment:dashboard")
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        return redirect("assessment:complete", session_id=session.id)
    assignment = session.current_assignment or session.assignments.first()
    return redirect("assessment:question", session_id=session.id, assignment_id=assignment.id)


@role_required("student")
def question_page(request, session_id, assignment_id):
    session = get_object_or_404(ExperimentSession.objects.select_related("version__experiment"), pk=session_id, student=request.user)
    pending_confidence = (
        ConfidenceAssignment.objects.select_related("attempt__assignment")
        .filter(
            attempt__session=session,
            attempt__status=Attempt.Status.SUBMITTED,
            condition=ConfidenceAssignment.Condition.PROMPT,
            response_status__in=(
                ConfidenceAssignment.ResponseStatus.ASSIGNED,
                ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN,
            ),
        )
        .order_by("attempt__submitted_at", "attempt_id")
        .first()
    )
    if (
        pending_confidence is not None
        and pending_confidence.attempt.assignment_id != assignment_id
    ):
        return redirect(
            "assessment:question",
            session_id=session.pk,
            assignment_id=pending_confidence.attempt.assignment_id,
        )
    assignment = get_object_or_404(StudentQuestionAssignment.objects.select_related("snapshot"), pk=assignment_id, session=session)
    reason = request.GET.get("reason", "navigation_click")
    try:
        attempt = record_visit(session.id, assignment.id, reason)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("assessment:complete", session_id=session.id)
    session.refresh_from_db()
    assignments = list(session.assignments.select_related("snapshot", "attempt"))
    remaining = None
    config = session.version.configuration
    if config.get("time_mode") == "per_question":
        remaining = max(0, int(config.get("per_question_time_limit_seconds") or 0) - attempt.active_seconds)
    elif config.get("time_mode") == "total" and session.deadline_at:
        from django.utils import timezone
        remaining = max(0, int((session.deadline_at - timezone.now()).total_seconds()))
    next_assignment = _next_assignment(session, assignment.id)
    fallback_next_url = reverse("assessment:question", args=[session.id, next_assignment.id]) if next_assignment else reverse("assessment:complete", args=[session.id])
    return render(request, "student/question.html", {
        "session": session,
        "assignment": assignment,
        "attempt": attempt,
        "assignments": assignments,
        "snapshot": assignment.snapshot,
        "remaining_seconds": remaining,
        "fallback_next_url": fallback_next_url,
        "pending_confidence": (
            _confidence_client_contract(attempt, pending_confidence)
            if pending_confidence is not None
            else None
        ),
    })


@role_required("student")
@require_POST
def save_draft_api(request, attempt_id):
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = save_draft(attempt.id, data.get("answer"), _client_time(data.get("client_occurred_at")))
        response = {"success": True, "saved_at": attempt.draft_saved_at.isoformat()}
        if attempt.saved_response_snapshot is not None:
            response.update(
                {
                    "response_snapshot_id": str(attempt.saved_response_snapshot.pk),
                    "content_hash": attempt.saved_response_snapshot.content_hash,
                    "content_length": attempt.saved_response_snapshot.content_length,
                    "state_hash": attempt.saved_response_snapshot.state_hash,
                }
            )
        elif attempt.saved_response_state is not None:
            response["response_state_after"] = attempt.saved_response_state
        elif attempt.assignment.snapshot.question_type in {
            "single_choice",
            "multiple_choice",
            "true_false",
        }:
            response["response_state_after"] = None
        return JsonResponse(response)
    except ValidationError as exc:
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


@role_required("student")
@require_POST
def submit_api(request, attempt_id):
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = submit_attempt(attempt.id, data.get("submission_token"), _client_time(data.get("client_clicked_at")))
        confidence = ensure_confidence_assignment(attempt)
        prompt_pending = (
            confidence.condition == confidence.Condition.PROMPT
            and confidence.response_status
            not in {
                confidence.ResponseStatus.COMMITTED,
                confidence.ResponseStatus.SKIPPED,
            }
        )
        next_url = _next_url_after_attempt(
            attempt,
            finish_if_last=not prompt_pending,
        )
        response = {
            "success": True,
            "status": attempt.status,
            "next_url": next_url,
            "confidence_required": prompt_pending,
        }
        if prompt_pending:
            response["confidence"] = _confidence_client_contract(
                attempt,
                confidence,
            )
        else:
            response.update(frozen_feedback_payload(attempt))
        return JsonResponse(response)
    except SubmissionValidationError as exc:
        record_submission_rejection(
            attempt_id=attempt.pk,
            submission_token=data.get("submission_token"),
            reason_code=exc.reason_code,
        )
        return JsonResponse(
            {"success": False, "error": "; ".join(exc.messages)},
            status=400,
        )
    except ValidationError as exc:
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=400)


@role_required("student")
@require_POST
def skip_api(request, attempt_id):
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    try:
        mark_skipped(attempt.id)
        next_assignment = _next_assignment(attempt.session, attempt.assignment_id)
        if next_assignment:
            next_url = reverse("assessment:question", args=[attempt.session_id, next_assignment.id])
        else:
            finish_session(attempt.session_id)
            next_url = reverse("assessment:complete", args=[attempt.session_id])
        return JsonResponse({"success": True, "next_url": next_url})
    except ValidationError as exc:
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


@role_required("student")
@require_POST
def finish(request, session_id):
    session = get_object_or_404(ExperimentSession, pk=session_id, student=request.user)
    finish_session(session.id)
    return redirect("assessment:complete", session_id=session.id)


@role_required("student")
def complete(request, session_id):
    session = get_object_or_404(ExperimentSession.objects.select_related("version__experiment"), pk=session_id, student=request.user)
    return render(request, "student/complete.html", {"session": session})
