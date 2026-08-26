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
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.analytics.export_catalog import COMMON_FIELD_DESCRIPTIONS, PLACEHOLDER_TABLES, TABLE_FIELD_DESCRIPTIONS, TABLE_LABELS, TABLE_PATHS
from apps.analytics.services import preview_export_table
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
    export_jobs = course.export_jobs.filter(status="succeeded").select_related("experiment", "research_study", "snapshot").order_by("-finished_at", "-created_at")
    latest_jobs = {}
    for job in export_jobs:
        key = (job.experiment_id, job.research_study_id, job.kind, job.package)
        latest_jobs.setdefault(key, job)
    experiment_exports = {
        experiment.pk: latest_jobs.get((experiment.pk, None, "research", "complete"))
        for experiment in experiments
    }
    for experiment in experiments:
        experiment.latest_export_job = experiment_exports[experiment.pk]
    selected_experiment = None
    selected_version = None
    selected_table = request.GET.get("table", "")
    view_mode = request.GET.get("view", "overview")
    experiment_id = request.GET.get("experiment_id", "")
    version_id = request.GET.get("version_id", "")
    if experiment_id:
        selected_experiment = experiments.filter(pk=experiment_id).first()
    if selected_experiment is not None:
        versions = selected_experiment.versions.order_by("version_number")
        if version_id:
            selected_version = versions.filter(pk=version_id).first()
        elif versions.count() == 1:
            selected_version = versions.first()
    else:
        versions = []
    version_required = selected_experiment is not None and versions.count() > 1 and selected_version is None
    selected_student_id = request.GET.get("student_id", "")
    selected_session_id = request.GET.get("session_id", "")
    selected_status = request.GET.get("status", "")
    preview = None
    if selected_table in TABLE_PATHS and selected_experiment is not None and not version_required:
        preview = preview_export_table(
            course=course,
            experiment=selected_experiment,
            table=selected_table,
            version_id=selected_version.pk if selected_version else version_id,
            student_id=selected_student_id,
            session_id=selected_session_id,
            status=selected_status,
            page=request.GET.get("page", 1),
        )
        preview["columns"] = [
            {
                "name": header,
                "description": TABLE_FIELD_DESCRIPTIONS.get(selected_table, {}).get(header, COMMON_FIELD_DESCRIPTIONS.get(header, "数据字段")),
            }
            for header in preview["headers"]
        ]
    preview_students = []
    preview_sessions = []
    if selected_experiment is not None:
        session_queryset = ExperimentSession.objects.filter(version__experiment=selected_experiment)
        if selected_version is not None:
            session_queryset = session_queryset.filter(version=selected_version)
        preview_sessions = list(session_queryset.select_related("student").order_by("pk"))
        seen_students = set()
        for session in preview_sessions:
            if session.student_id not in seen_students:
                preview_students.append(session.student)
                seen_students.add(session.student_id)
    table_groups = [
        {"key": "core", "label": "核心数据", "items": [(key, TABLE_LABELS[key]) for key in ("subjects", "sessions", "assignments", "item_instances", "attempts")]},
        {"key": "raw", "label": "原始过程", "items": [(key, TABLE_LABELS[key]) for key in ("assessment_events", "response_events", "navigation_events", "exposure_events", "telemetry_events")]},
        {"key": "design", "label": "测量设计", "items": [(key, TABLE_LABELS[key]) for key in ("item_snapshots", "kc_dictionary", "item_kc_mapping", "q_matrix")]},
        {"key": "self_report", "label": "自报告与仿真", "items": [(key, TABLE_LABELS[key]) for key in ("confidence_assignments", "simulation_events", "simulation_snapshots")]},
        {"key": "derived", "label": "派生数据", "items": [(key, TABLE_LABELS[key]) for key in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features")]},
    ]
    quick_exports = [
        ("complete", "完整去标识化研究包"),
        ("kt", "标准 KT 包"),
        ("cd", "CD 包"),
        ("process", "作答过程包"),
        ("confidence", "Confidence / 元认知包"),
        ("simulation", "仿真包"),
    ]
    for group in table_groups:
        group["items"] = [
            {"key": key, "label": label, "filename": TABLE_PATHS[key].split("/")[-1], "placeholder": key in PLACEHOLDER_TABLES}
            for key, label in group["items"]
        ]
    status_options = [("", "全部状态")]
    status_options += list(ExperimentSession.Status.choices)
    status_options += [(value, label) for value, label in Attempt.Status.choices]
    scoped_sessions = ExperimentSession.objects.filter(version__experiment=selected_experiment) if selected_experiment else ExperimentSession.objects.none()
    if selected_version is not None:
        scoped_sessions = scoped_sessions.filter(version=selected_version)
    overview = {
        "session_count": scoped_sessions.count(),
        "student_count": scoped_sessions.values("student_id").distinct().count(),
        "assignment_count": StudentQuestionAssignment.objects.filter(session__in=scoped_sessions).count(),
        "attempt_count": Attempt.objects.filter(session__in=scoped_sessions).count(),
        "event_count": AssessmentEvent.objects.filter(session__in=scoped_sessions).count(),
    }
    quality = _quality_for_course(course, selected_experiment, selected_version)
    return render(
        request,
        "teacher/course_data.html",
        {
            "course": course,
            "current_course": course,
            "experiments": experiments,
            "export_jobs": list(latest_jobs.values()),
            "longitudinal_studies": course.research_study_links.select_related("study")[:20],
            "data_tree": table_groups,
            "quick_exports": quick_exports,
            "selected_table": selected_table,
            "selected_table_label": TABLE_LABELS.get(selected_table, selected_table),
            "selected_experiment": selected_experiment,
            "selected_version": selected_version,
            "version_required": version_required,
            "versions": versions,
            "preview_students": preview_students,
            "preview_sessions": preview_sessions,
            "status_options": status_options,
            "selected_student_id": selected_student_id,
            "selected_session_id": selected_session_id,
            "selected_status": selected_status,
            "preview": preview,
            "view_mode": view_mode,
            "overview": overview,
            "quality": quality,
            "field_descriptions": TABLE_FIELD_DESCRIPTIONS,
            "table_labels": TABLE_LABELS,
        },
    )


def _quality_for_course(course, experiment=None, version=None):
    events = AssessmentEvent.objects.filter(session__version__experiment__course=course)
    if experiment is not None:
        events = events.filter(session__version__experiment=experiment)
    if version is not None:
        events = events.filter(session__version=version)
    event_rows = list(events.values_list("quality_flags", "capture_mode", "client_instance_id", "client_sequence_no"))
    total = len(event_rows)
    keyed = [(instance, sequence) for _flags, _mode, instance, sequence in event_rows if instance and sequence is not None]
    duplicate_count = len(keyed) - len(set(keyed))
    gaps = 0
    by_instance = {}
    for _flags, _mode, instance, sequence in event_rows:
        if instance and sequence is not None:
            by_instance.setdefault(instance, set()).add(int(sequence))
    for sequence_set in by_instance.values():
        if sequence_set:
            gaps += max(sequence_set) - min(sequence_set) + 1 - len(sequence_set)
    clock_count = sum("clock_unsynchronized" in (flags or []) for flags, *_ in event_rows)
    offline_count = sum("offline_buffered" in (flags or []) for flags, *_ in event_rows)
    native_count = sum(mode == AssessmentEvent.CaptureMode.NATIVE for _flags, mode, *_ in event_rows)
    legacy_count = sum(mode == AssessmentEvent.CaptureMode.LEGACY_PROJECTION for _flags, mode, *_ in event_rows)
    process_events = list(events.values_list("attempt_id", "event_type", "server_ingest_sequence_no"))
    committed_attempt_ids = {attempt for attempt, event_type, _seq in process_events if attempt and event_type == "response_committed"}
    business_filter = {"attempt__session__version__experiment__course": course}
    if experiment is not None:
        business_filter["attempt__session__version__experiment"] = experiment
    if version is not None:
        business_filter["attempt__session__version"] = version
    business_committed_ids = set(SubmissionEvent.objects.filter(**business_filter).values_list("attempt_id", flat=True))
    feedback_violations = sum(
        1 for attempt, event_type, seq in process_events
        if attempt and event_type == "feedback_shown" and not any(a == attempt and e == "response_committed" and s < seq for a, e, s in process_events)
    )
    return {
        "event_count": total,
        "duplicate_count": duplicate_count,
        "sequence_gap_count": gaps,
        "clock_unsynchronized_rate": round(clock_count / total * 100, 2) if total else 0,
        "offline_rate": round(offline_count / total * 100, 2) if total else 0,
        "native_count": native_count,
        "legacy_count": legacy_count,
        "feedback_order_violations": feedback_violations,
        "response_commit_mismatch": len(committed_attempt_ids ^ business_committed_ids),
    }


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_quality(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    quality = _quality_for_course(course)
    context = {
        "course": course,
        "current_course": course,
        "quality": quality,
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
