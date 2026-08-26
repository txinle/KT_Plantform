from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.audit.services import log_action

from .course_access import (
    accessible_courses,
    get_accessible_course,
    lock_course,
    require_course_manager,
    require_writable_course,
)
from .course_revision import course_revision_value
from .forms import (
    CourseForm,
    CourseSettingsForm,
    course_settings_state_token_matches,
)
from .models import Course, User, DataPurgeRequest
from apps.assessment.models import AssessmentEvent, SubmissionEvent
from .permissions import role_required


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_list(request):
    courses = accessible_courses(request.user).annotate(
        class_count=Count("classes", distinct=True),
        bank_count=Count("question_banks", distinct=True),
        experiment_count=Count("experiments", distinct=True),
    )
    return render(request, "teacher/course_list.html", {"courses": courses})


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_create(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save(commit=False)
        course.owner = request.user
        course.save()
        log_action(
            request,
            "course.create",
            course,
            {"code": course.code, "term": course.term},
        )
        messages.success(request, "课程已创建。")
        return redirect("courses:detail", course.pk)
    return render(request, "teacher/course_form.html", {"form": form})


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_detail(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    recent_experiments = course.experiments.select_related("question_bank")[:5]
    recent_banks = course.question_banks.annotate(
        question_count=Count("questions", distinct=True),
    )[:5]
    return render(
        request,
        "teacher/course_detail.html",
        {
            "course": course,
            "current_course": course,
            "class_count": course.classes.count(),
            "bank_count": course.question_banks.count(),
            "experiment_count": course.experiments.count(),
            "student_count": course.enrollments.filter(is_active=True).count(),
            "recent_experiments": recent_experiments,
            "recent_banks": recent_banks,
            "is_course_owner": request.user.is_superuser
            or request.user.role == User.Role.ADMIN
            or course.owner_id == request.user.id,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_revision(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    response = JsonResponse({"revision": course_revision_value(course)})
    response["Cache-Control"] = "no-store"
    return response


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    experiments = course.experiments.annotate(
        version_count=Count("versions", distinct=True),
        session_count=Count("versions__sessions", distinct=True),
    )
    export_jobs = course.export_jobs.select_related("experiment", "snapshot")[:10]
    return render(
        request,
        "teacher/course_data.html",
        {
            "course": course,
            "current_course": course,
            "experiments": experiments,
            "export_jobs": export_jobs,
            "longitudinal_studies": course.research_study_links.select_related("study")[:20],
            "data_tree": [
                {"key": "overview", "label": "数据概览", "items": []},
                {"key": "quality", "label": "采集质量", "items": []},
                {"key": "quick", "label": "快速导出", "items": [
                    ("complete", "完整去标识化研究包"),
                    ("kt", "标准 KT 包"),
                    ("cd", "CD 包"),
                    ("process", "作答过程包"),
                    ("confidence", "Confidence / 元认知包"),
                    ("simulation", "仿真包"),
                ]},
                {"key": "core", "label": "核心表", "items": [(k, k) for k in ("subjects", "sessions", "assignments", "item_instances", "attempts")]},
                {"key": "raw", "label": "原始过程", "items": [(k, k) for k in ("assessment_events", "response_events", "navigation_events", "exposure_events", "telemetry_events")]},
                {"key": "design", "label": "测量设计", "items": [(k, k) for k in ("item_snapshots", "kc_dictionary", "item_kc_mapping", "q_matrix")]},
                {"key": "derived", "label": "派生数据", "items": [(k, k) for k in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features")]},
                {"key": "compat", "label": "兼容导出", "items": [("legacy_kt", "旧匿名 KT CSV"), ("legacy_navigation", "旧匿名导航 CSV"), ("legacy_identified", "旧实名可选字段 Excel")]},
                {"key": "longitudinal", "label": "纵向研究项目", "items": []},
                {"key": "jobs", "label": "导出任务", "items": []},
            ],
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_quality(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    events = AssessmentEvent.objects.filter(session__version__experiment__course=course)
    event_rows = list(events.values_list("quality_flags", "capture_mode", "client_instance_id", "client_sequence_no"))
    total = len(event_rows)
    duplicate_keys = len(event_rows) - len({(instance, sequence) for _flags, _mode, instance, sequence in event_rows if instance and sequence})
    gaps = 0
    by_instance = {}
    for _flags, _mode, instance, sequence in event_rows:
        if instance and sequence:
            by_instance.setdefault(instance, set()).add(int(sequence))
    for sequence_set in by_instance.values():
        if sequence_set:
            # Sequences may start at an arbitrary client value; only missing
            # values inside the observed range count as gaps.
            gaps += max(sequence_set) - min(sequence_set) + 1 - len(sequence_set)
    clock_count = sum("clock_unsynchronized" in (flags or []) for flags, *_ in event_rows)
    offline_count = sum("offline_buffered" in (flags or []) for flags, *_ in event_rows)
    native_count = sum(mode == AssessmentEvent.CaptureMode.NATIVE for _flags, mode, *_ in event_rows)
    legacy_count = sum(mode == AssessmentEvent.CaptureMode.LEGACY_PROJECTION for _flags, mode, *_ in event_rows)
    process_events = list(events.values_list("attempt_id", "event_type", "server_ingest_sequence_no"))
    response_commits = {(attempt, seq) for attempt, event_type, seq in process_events if attempt and event_type == "response_committed"}
    feedback_violations = sum(
        1 for attempt, event_type, seq in process_events
        if attempt and event_type == "feedback_shown" and not any(a == attempt and e == "response_committed" and s < seq for a, e, s in process_events)
    )
    committed_attempt_ids = {attempt for attempt, event_type, _seq in process_events if attempt and event_type == "response_committed"}
    business_committed_ids = set(SubmissionEvent.objects.filter(attempt__session__version__experiment__course=course).values_list("attempt_id", flat=True))
    commit_mismatch = len(committed_attempt_ids ^ business_committed_ids)
    context = {
        "course": course,
        "current_course": course,
        "quality": {
            "event_count": total,
            "duplicate_count": duplicate_keys,
            "sequence_gap_count": gaps,
            "clock_unsynchronized_rate": round(clock_count / total * 100, 2) if total else 0,
            "offline_rate": round(offline_count / total * 100, 2) if total else 0,
            "native_count": native_count,
            "legacy_count": legacy_count,
            "feedback_order_violations": feedback_violations,
            "response_commit_mismatch": commit_mismatch,
        },
    }
    return render(request, "teacher/course_quality.html", context)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_settings(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_manager(course, request.user)
    form = CourseSettingsForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        saved = False
        with transaction.atomic():
            course = lock_course(course)
            require_course_manager(course, request.user)
            require_writable_course(course)
            form = CourseSettingsForm(request.POST, instance=course)
            if not course_settings_state_token_matches(
                request.POST.get("state_token", ""), course
            ):
                form.add_error(
                    None, "课程设置已被其他管理者更新，请刷新后重新提交。"
                )
            elif form.is_valid():
                previous_teacher_ids = set(
                    course.teacher_links.values_list("teacher_id", flat=True)
                )
                course = form.save()
                teacher_ids = set(
                    form.cleaned_data["teachers"].values_list("pk", flat=True)
                )
                log_action(
                    request,
                    "course.update",
                    course,
                    {"course_id": course.pk, "teacher_ids": sorted(teacher_ids)},
                )
                if teacher_ids != previous_teacher_ids:
                    log_action(
                        request,
                        "course.collaborators_update",
                        course,
                        {
                            "course_id": course.pk,
                            "added_teacher_ids": sorted(
                                teacher_ids - previous_teacher_ids
                            ),
                            "removed_teacher_ids": sorted(
                                previous_teacher_ids - teacher_ids
                            ),
                        },
                    )
                saved = True
        if saved:
            messages.success(request, "课程设置已保存。")
            return redirect("courses:settings", course.pk)
    return render(
        request,
        "teacher/course_settings.html",
        {"course": course, "current_course": course, "form": form, "is_course_owner": True, "purge_requests": DataPurgeRequest.objects.filter(scope_definition__course_id=course.pk)[:10]},
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_archive(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_manager(course, request.user)
    with transaction.atomic():
        course = lock_course(course)
        require_course_manager(course, request.user)
        if course.status != Course.Status.ARCHIVED:
            course.status = Course.Status.ARCHIVED
            course.save(update_fields=["status", "updated_at"])
            log_action(request, "course.archive", course)
            messages.success(request, "课程已归档并切换为只读。")
    return redirect("courses:detail", course.pk)
