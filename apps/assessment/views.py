import json
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from apps.accounts.permissions import role_required
from apps.accounts.models import Course, CourseEnrollment
from apps.api.rollout import is_vue_module_enabled
from apps.api.shell import render_vue_shell
from apps.experiments.models import ClassExperimentTask, Experiment
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
from .response_snapshot_services import canonical_response_state
from .writer_lease_services import WriterLeaseConflict
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
    if is_vue_module_enabled("student_courses", request.user):
        return render_vue_shell(
            request,
            module="student-courses",
            title="我的课程 | KT Data Lab",
        )
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
    if is_vue_module_enabled(
        "student_courses",
        request.user,
        course=enrollment.course,
    ):
        return render_vue_shell(
            request,
            module="student-courses",
            title=f"{enrollment.course.name} | 我的课程",
            course=enrollment.course,
        )
    from .api.views import _student_task_queryset, _task_projection

    status_labels = {
        "scheduled": "未开始",
        "open": "进行中",
        "closed": "已结束",
        "cancelled": "已取消",
    }
    cards = []
    for task in _student_task_queryset(request.user, course_id=enrollment.course_id):
        projection = _task_projection(task, course=enrollment.course)
        configuration = task.release_snapshot.configuration
        session = next(iter(task.current_student_sessions), None)
        status_label = status_labels.get(
            projection["business_status"], projection["business_status"]
        )
        if session is not None and session.status != ExperimentSession.Status.IN_PROGRESS:
            status_label = session.get_status_display()
        cards.append(
            {
                "experiment": task.experiment,
                "session": session,
                "question_count": projection["progress"]["total"],
                "primary_action": projection["primary_action"],
                "status_label": status_label,
                "status_tone": (
                    "success"
                    if projection["primary_action"] == "view_result"
                    else "info"
                    if projection["business_status"] == "open"
                    else "neutral"
                ),
                "time_mode_label": Experiment.TimeMode(
                    configuration.get("time_mode", Experiment.TimeMode.NONE)
                ).label,
                "feedback_mode_label": Experiment.Feedback(
                    configuration.get("feedback_mode", Experiment.Feedback.NONE)
                ).label,
                "resume_mode_label": Experiment.ResumeMode(
                    configuration.get("resume_mode", Experiment.ResumeMode.RESUMABLE)
                ).label,
            }
        )
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
    if is_vue_module_enabled(
        "student_courses",
        request.user,
        course=enrollment.course,
    ):
        return render_vue_shell(
            request,
            module="student-courses",
            title=f"我的学习 | {enrollment.course.name}",
            course=enrollment.course,
        )
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
    if is_vue_module_enabled(
        "student_courses",
        request.user,
        course=enrollment.course,
    ):
        return render_vue_shell(
            request,
            module="student-courses",
            title=f"实验结果 | {enrollment.course.name}",
            course=enrollment.course,
        )
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
    if is_vue_module_enabled(
        "student_courses",
        request.user,
        course=enrollment.course,
    ):
        return render_vue_shell(
            request,
            module="student-courses",
            title=f"题目结果 | {enrollment.course.name}",
            course=enrollment.course,
            classic_scripts=(f"{static('js/process-event-collector.js')}?v=20260906.capture-v2",),
        )
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


@require_POST
@role_required("student")
def start_experiment(request, experiment_id):
    request_received_at = timezone.now()
    experiment = get_object_or_404(Experiment.objects.select_related("course"), pk=experiment_id)
    tasks = list(
        ClassExperimentTask.objects.filter(
            experiment=experiment,
            participant_snapshots__student=request.user,
            participant_snapshots__version_id=F("release_snapshot_id"),
            participant_snapshots__class_snapshot_id=F("class_snapshot_id"),
        )
        .distinct()
        .order_by("pk")[:2]
    )
    if len(tasks) != 1:
        messages.error(request, "无法确定唯一的班级实验任务，请返回课程重新进入。")
        return redirect("assessment:dashboard")
    try:
        session = get_or_create_session(
            experiment,
            request.user,
            request_received_at=request_received_at,
            class_task=tasks[0],
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("assessment:dashboard")
    if session.status != ExperimentSession.Status.IN_PROGRESS:
        return redirect("assessment:complete", session_id=session.id)
    assignment = session.current_assignment or session.assignments.first()
    return redirect("assessment:question", session_id=session.id, assignment_id=assignment.id)


@role_required("student")
def question_page(request, session_id, assignment_id):
    request_received_at = timezone.now()
    session = get_object_or_404(
        ExperimentSession.objects.select_related(
            "version__experiment",
            "class_task__class_group",
            "class_task__experiment",
        ),
        pk=session_id,
        student=request.user,
    )
    if getattr(request, '_kt_standalone', False) or is_vue_module_enabled(
        "student_assessment",
        request.user,
        task=session.class_task,
    ):
        get_object_or_404(
            StudentQuestionAssignment,
            pk=assignment_id,
            session=session,
        )
        response = render_vue_shell(
            request,
            module="student-assessment",
            title=f"作答 | {session.version.experiment.name}",
            classic_scripts=(
                f"{static('js/process-event-collector.js')}?v=20260906.capture-v2",
            ),
        )
        if session.capture_contract_id and session.capture_contract.contract_name == 'capture-v2':
            response['X-Capture-Contract'] = 'capture-v2'
        return response
    from .capture_v2_services import uses_capture_v2
    if uses_capture_v2(session):
        return HttpResponse('当前采集版本暂不可用。请保留本机作答记录，稍后从此任务继续。', status=503)
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
        attempt = record_visit(
            session.id,
            assignment.id,
            reason,
            request_received_at=request_received_at,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("assessment:complete", session_id=session.id)
    session.refresh_from_db()
    assignments = list(session.assignments.select_related("snapshot", "attempt"))
    remaining = None
    config = session.version.configuration
    if attempt.initial_effective_deadline_at:
        remaining = max(
            0,
            int((attempt.initial_effective_deadline_at - timezone.now()).total_seconds()),
        )
    elif config.get("time_mode") == "per_question":
        remaining = max(0, int(config.get("per_question_time_limit_seconds") or 0) - attempt.active_seconds)
    elif config.get("time_mode") == "total" and session.deadline_at:
        remaining = max(0, int((session.deadline_at - timezone.now()).total_seconds()))
    next_assignment = _next_assignment(session, assignment.id)
    fallback_next_url = reverse("assessment:question", args=[session.id, next_assignment.id]) if next_assignment else reverse("assessment:complete", args=[session.id])
    initial_response_state = None
    question_type = assignment.snapshot.question_type
    if question_type == "short_answer" and attempt.saved_response_snapshot is not None:
        saved = attempt.saved_response_snapshot
        initial_response_state = {
            "snapshot_id": str(saved.pk),
            "state_hash": saved.state_hash,
            "content_hash": saved.content_hash,
            "content_length": saved.content_length,
            "schema_version": saved.schema_version,
        }
    elif attempt.draft_answer is not None:
        initial_response_state = canonical_response_state(
            question_type, attempt.draft_answer
        )
    return render(request, "student/question.html", {
        "session": session,
        "assignment": assignment,
        "attempt": attempt,
        "assignments": assignments,
        "snapshot": assignment.snapshot,
        "remaining_seconds": remaining,
        "fallback_next_url": fallback_next_url,
        "initial_response_state": initial_response_state,
        "pending_confidence": (
            _confidence_client_contract(attempt, pending_confidence)
            if pending_confidence is not None
            else None
        ),
    })


@role_required("student")
@require_POST
def save_draft_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = save_draft(
            attempt.id,
            data.get("answer"),
            _client_time(data.get("client_occurred_at")),
            request_received_at=request_received_at,
            target_event_id=data.get('target_event_id'),
            expected_version=data.get('expected_version'),
            writer_lease_token=data.get('writer_lease_token'),
            writer_client_instance_id=data.get('writer_client_instance_id'),
        )
        response = {"success": True, "saved_at": attempt.draft_saved_at.isoformat(),
                    "response_version": attempt.response_version,
                    "active_response_event_id": str(attempt.active_response_event_id) if attempt.active_response_event_id else None}
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
    except WriterLeaseConflict:
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'}}, status=409)
    except ValidationError as exc:
        from .capture_v2_services import (
            ResponseVersionConflict,
            record_response_pointer_conflict,
        )
        if isinstance(exc, ResponseVersionConflict):
            record_response_pointer_conflict(
                attempt=attempt,
                conflict=exc,
                requested_expected_version=data.get('expected_version'),
                request_client_instance_id=data.get('writer_client_instance_id'),
            )
            return JsonResponse({'success': False, 'error': {'code': 'RESPONSE_VERSION_CONFLICT', 'message': str(exc), 'fields': exc.document}}, status=409)
        return JsonResponse({"success": False, "error": "; ".join(exc.messages)}, status=409)


@role_required("student")
@require_POST
def submit_api(request, attempt_id):
    request_received_at = timezone.now()
    attempt = get_object_or_404(Attempt, pk=attempt_id, session__student=request.user)
    data = _json(request)
    try:
        attempt = submit_attempt(
            attempt.id,
            data.get("submission_token"),
            _client_time(data.get("client_clicked_at")),
            request_received_at=request_received_at,
            target_event_id=data.get('target_event_id'),
            writer_lease_token=data.get('writer_lease_token'),
            writer_client_instance_id=data.get('writer_client_instance_id'),
        )
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
    except WriterLeaseConflict:
        return JsonResponse({'success': False, 'error': {'code': 'writer_lease_conflict', 'message': '另一设备正在保存此题，请稍后重试；本机轨迹仍保留。'}}, status=409)
    except SubmissionValidationError as exc:
        record_submission_rejection(
            attempt_id=attempt.pk,
            submission_token=data.get("submission_token"),
            reason_code=exc.reason_code,
        )
        return JsonResponse(
            {
                "success": False,
                "error": "; ".join(exc.messages),
                "code": exc.reason_code.upper(),
            },
            status=409 if exc.reason_code == "task_terminated" else 400,
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
