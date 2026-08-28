from django.contrib import messages
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

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
from .models import ClassGroup, Course, User, DataPurgeRequest
from apps.assessment.models import AssessmentEvent, SubmissionEvent
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.analytics.export_catalog import COMMON_FIELD_DESCRIPTIONS, PLACEHOLDER_TABLES, TABLE_FIELD_DESCRIPTIONS, TABLE_LABELS, TABLE_PATHS
from apps.analytics.advanced_catalog import build_advanced_catalog, enabled_extension_tables
from apps.analytics.services import UNRECORDED_CLASS_FILTER, preview_export_table
from apps.analytics.data_scope import DataScope, ScopeNotFound, ScopeValidationError
from apps.analytics.quality_reports import EvaluationState, build_quality_report
from apps.analytics.teaching_data import (
    attempt_timeline,
    build_teaching_roster,
    effective_grade_for_attempt,
    summarize_attempt_timing,
)
from apps.analytics.teaching_scope import TeachingScope
from apps.experiments.models import Condition, ExperimentClassSnapshot
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
    selected_class_name = request.GET.get("class_name", "")
    selected_student_number = request.GET.get("student_number", "")
    selected_session_id = request.GET.get("session_id", "")
    selected_status = request.GET.get("status", "")
    preview = None
    if selected_table in TABLE_PATHS and selected_experiment is not None and not version_required:
        preview = preview_export_table(
            course=course,
            experiment=selected_experiment,
            table=selected_table,
            version_id=selected_version.pk if selected_version else version_id,
            class_name=selected_class_name,
            student_number=selected_student_number,
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
    class_snapshot_names = []
    preview_sessions = []
    if selected_experiment is not None:
        session_queryset = ExperimentSession.objects.filter(version__experiment=selected_experiment)
        if selected_version is not None:
            session_queryset = session_queryset.filter(version=selected_version)
        preview_sessions = list(session_queryset.select_related("student").order_by("pk"))
        seen_student_class_snapshots = set()
        seen_class_snapshot_values = set()
        for session in preview_sessions:
            class_filter_value = session.class_snapshot_name or UNRECORDED_CLASS_FILTER
            if class_filter_value not in seen_class_snapshot_values:
                class_snapshot_names.append({
                    "value": class_filter_value,
                    "label": session.class_snapshot_name or "未记录班级",
                })
                seen_class_snapshot_values.add(class_filter_value)
            student_class_snapshot = (session.student_id, session.class_snapshot_name)
            if student_class_snapshot not in seen_student_class_snapshots:
                preview_students.append({
                    "student": session.student,
                    "class_name": session.class_snapshot_name,
                })
                seen_student_class_snapshots.add(student_class_snapshot)
    table_groups = [
        {"key": "core", "label": "核心数据", "items": [(key, TABLE_LABELS[key]) for key in ("subjects", "sessions", "assignments", "item_instances", "attempts")]},
        {"key": "raw", "label": "原始过程", "items": [(key, TABLE_LABELS[key]) for key in ("assessment_events", "response_events", "navigation_events", "exposure_events", "telemetry_events")]},
        {"key": "design", "label": "测量设计", "items": [(key, TABLE_LABELS[key]) for key in ("item_snapshots", "kc_dictionary", "item_kc_mapping", "q_matrix")]},
        {"key": "self_report", "label": "自报告与仿真", "items": [(key, TABLE_LABELS[key]) for key in ("confidence_assignments", "simulation_events", "simulation_snapshots")]},
        {"key": "derived", "label": "派生数据", "items": [(key, TABLE_LABELS[key]) for key in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features")]},
    ]
    quick_exports = [
        ("complete", "完整实名研究包"),
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
            "class_snapshot_names": class_snapshot_names,
            "preview_sessions": preview_sessions,
            "status_options": status_options,
            "selected_class_name": selected_class_name,
            "selected_student_number": selected_student_number,
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


# Data-center v2 ----------------------------------------------------------
# The legacy implementations above remain in the source temporarily so that
# old imports retain a stable module surface.  These definitions intentionally
# replace the public view functions with the task-oriented workbench routes.

_DATA_SCOPE_SESSION_KEY = "data_center_scope_v1"
_TEACHING_SCOPE_SESSION_KEY = "data_center_teaching_scope_v1"
_TEACHING_SCOPE_KEYS = ("experiment_id", "class_id")
_DATA_SCOPE_KEYS = (
    "scope_version",
    "experiment_id",
    "version_mode",
    "version_id",
    "class_id",
    "class_snapshot_id",
    "student_id",
    "session_id",
    "condition_id",
    "session_status",
    "attempt_status",
    "started_from_utc",
    "started_before_utc",
)
_LEGACY_SCOPE_KEYS = ("class_name", "student_number", "status")


def _scope_from_request(request, course):
    temporary_values = {
        key: request.GET.get(key)
        for key in (*_DATA_SCOPE_KEYS, *_LEGACY_SCOPE_KEYS)
        if request.GET.get(key) not in (None, "")
    }
    values = temporary_values or request.session.get(
        f"{_DATA_SCOPE_SESSION_KEY}:{course.pk}", {}
    )
    if not values:
        teaching_values = request.session.get(
            f"{_TEACHING_SCOPE_SESSION_KEY}:{course.pk}", {}
        )
        teaching_scope = TeachingScope.from_mapping(course, teaching_values)
        return _research_scope_for_teaching(teaching_scope)
    try:
        return DataScope.from_mapping(course, values)
    except ScopeNotFound as error:
        raise Http404("数据范围不属于当前课程") from error
    except ScopeValidationError as error:
        raise SuspiciousOperation(str(error)) from error


def _teaching_scope_from_request(request, course):
    temporary_values = {
        key: request.GET.get(key)
        for key in _TEACHING_SCOPE_KEYS
        if request.GET.get(key) not in (None, "")
    }
    if (
        "class_id" not in temporary_values
        and request.GET.get("class_snapshot_id")
    ):
        snapshot = (
            ExperimentClassSnapshot.objects.select_related("version__experiment")
            .filter(
                pk=request.GET["class_snapshot_id"],
                version__experiment__course=course,
            )
            .first()
        )
        if snapshot and snapshot.source_class_group_id:
            temporary_values.setdefault(
                "experiment_id", snapshot.version.experiment_id
            )
            temporary_values["class_id"] = snapshot.source_class_group_id
    values = temporary_values or request.session.get(
        f"{_TEACHING_SCOPE_SESSION_KEY}:{course.pk}", {}
    )
    try:
        return TeachingScope.from_mapping(course, values)
    except ScopeNotFound as error:
        raise Http404("教学范围不属于当前课程") from error
    except ScopeValidationError as error:
        raise SuspiciousOperation(str(error)) from error


def _research_scope_for_teaching(teaching_scope):
    values = {}
    if teaching_scope.experiment_id is not None:
        values["experiment_id"] = teaching_scope.experiment_id
    if teaching_scope.class_id is not None:
        values["class_id"] = teaching_scope.class_id
    return DataScope.from_mapping(teaching_scope.course, values)


def _scope_display(scope):
    parts = []
    if scope.experiment_id:
        from apps.experiments.models import Experiment

        experiment = Experiment.objects.filter(pk=scope.experiment_id).first()
        if experiment:
            parts.append(experiment.name)
    if scope.version_id:
        from apps.experiments.models import ExperimentVersion

        version = ExperimentVersion.objects.filter(pk=scope.version_id).first()
        if version:
            parts.append(f"Version {version.version_number}")
    elif scope.version_mode == "all":
        parts.append("全部版本")
    if scope.class_snapshot_id:
        snapshot = ExperimentClassSnapshot.objects.filter(pk=scope.class_snapshot_id).first()
        if snapshot:
            parts.append(snapshot.display_name or "未记录班级")
    elif scope.class_id:
        class_group = scope.course.classes.filter(pk=scope.class_id).first()
        if class_group:
            parts.append(class_group.name)
    if scope.student_id:
        student = User.objects.filter(pk=scope.student_id).first()
        if student:
            parts.append(student.shown_name)
    if scope.session_id:
        parts.append(f"Session {scope.session_id}")
    return " · ".join(parts) if parts else "当前课程 · 全部实验"


def _teaching_scope_display(scope):
    parts = []
    if scope.experiment_id:
        experiment = scope.course.experiments.filter(pk=scope.experiment_id).first()
        if experiment:
            parts.append(experiment.name)
    if scope.class_id:
        class_group = scope.course.classes.filter(pk=scope.class_id).first()
        if class_group:
            parts.append(class_group.name)
    return " · ".join(parts) if parts else "请选择实验和班级"


def _data_center_context(
    request,
    course,
    scope,
    *,
    active,
    teaching_scope=None,
    research_scope=False,
):
    navigation = [
        ("overview", "概览", "courses:data"),
        ("teaching", "教学数据", "courses:data_teaching"),
        ("exports", "导出", "courses:data_exports"),
        ("quality", "数据质量", "courses:data_quality"),
        ("history", "导出记录", "courses:data_export_history"),
        ("advanced", "高级数据工具", "courses:data_advanced"),
    ]
    teaching_scope = teaching_scope or (
        scope if isinstance(scope, TeachingScope) else _teaching_scope_from_request(request, course)
    )
    serialized_scope = (
        scope.serialize()
        if research_scope and isinstance(scope, DataScope)
        else teaching_scope.serialize()
    )
    scope_query = urlencode(serialized_scope)
    temporary_scope = any(
        request.GET.get(key) not in (None, "") for key in _TEACHING_SCOPE_KEYS
    )
    remembered_scope = request.session.get(
        f"{_TEACHING_SCOPE_SESSION_KEY}:{course.pk}"
    )
    return {
        "course": course,
        "current_course": course,
        "data_navigation": [
            {
                "key": key,
                "label": label,
                "url": (
                    f"{reverse(url_name, args=[course.pk])}?{scope_query}"
                    if research_scope
                    and scope_query
                    and key in {"exports", "quality", "advanced"}
                    else reverse(url_name, args=[course.pk])
                ),
                "active": key == active,
            }
            for key, label, url_name in navigation
        ],
        "scope": scope,
        "teaching_scope": teaching_scope,
        "scope_display": (
            _scope_display(scope)
            if research_scope and isinstance(scope, DataScope)
            else _teaching_scope_display(teaching_scope)
        ),
        "scope_values": list(scope.serialize().items()),
        "scope_query": scope_query,
        "scope_is_temporary": temporary_scope,
        "scope_has_remembered_default": bool(remembered_scope),
        "research_scope": research_scope,
        "experiments": course.experiments.prefetch_related("versions").order_by("name"),
        "teaching_classes": course.classes.filter(is_active=True).order_by(
            "academic_year", "name", "pk"
        ),
        "class_snapshots": ExperimentClassSnapshot.objects.filter(
            version__experiment__course=course
        ).select_related("version__experiment").order_by(
            "version__experiment__name", "version__version_number", "academic_year_snapshot", "name_snapshot"
        ),
        "scope_students": User.objects.filter(
            assessment_sessions__version__experiment__course=course
        ).distinct().order_by("student_number", "display_name", "username"),
        "scope_conditions": Condition.objects.filter(
            experiment__course=course
        ).select_related("experiment").order_by("experiment__name", "key"),
        "session_status_choices": ExperimentSession.Status.choices,
        "attempt_status_choices": Attempt.Status.choices,
    }


def _render_data_center(
    request,
    template,
    course,
    scope,
    *,
    active,
    teaching_scope=None,
    research_scope=False,
    **context,
):
    shared = _data_center_context(
        request,
        course,
        scope,
        active=active,
        teaching_scope=teaching_scope,
        research_scope=research_scope,
    )
    shared.update(context)
    return render(request, template, shared)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data(request, course_pk):
    """Default workbench: operational facts, not database-table navigation."""
    course = get_accessible_course(request.user, course_pk)
    if request.GET.get("table"):
        query = request.GET.urlencode()
        return redirect(f"{reverse('courses:data_advanced', args=[course.pk])}?{query}")
    if request.GET.get("view") == "quality":
        query = request.GET.copy()
        query.pop("view", None)
        suffix = f"?{query.urlencode()}" if query else ""
        return redirect(f"{reverse('courses:data_quality', args=[course.pk])}{suffix}")
    scope = _teaching_scope_from_request(request, course)
    sessions = scope.sessions()
    attempts = scope.attempts()
    quality_report = build_quality_report(scope)
    warning_attempt_ids = {
        row.attempt_id
        for row in quality_report.rows
        if row.attempt_id is not None
        and row.state in {EvaluationState.WARN, EvaluationState.FAIL}
    }
    facts = {
        "participants": scope.roster().count(),
        "observed_participants": sessions.values("student_id").distinct().count(),
        "completed_sessions": sessions.filter(
            status=ExperimentSession.Status.COMPLETED
        ).count(),
        "total_sessions": sessions.count(),
        "submitted_attempts": SubmissionEvent.objects.filter(
            attempt__in=attempts
        ).count(),
        "quality_warnings": len(warning_attempt_ids),
        "not_evaluable": quality_report.counts.get(EvaluationState.NOT_EVALUABLE, 0),
    }
    return _render_data_center(
        request,
        "teacher/data_center/overview.html",
        course,
        scope,
        active="overview",
        facts=facts,
        recent_jobs=course.export_jobs.select_related("experiment").order_by("-created_at")[:5],
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_POST
def course_data_scope_update(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    try:
        scope = TeachingScope.from_mapping(course, request.POST)
    except ScopeNotFound:
        return HttpResponseBadRequest("数据范围不属于当前课程")
    except ScopeValidationError as error:
        return HttpResponseBadRequest(str(error))
    request.session[f"{_TEACHING_SCOPE_SESSION_KEY}:{course.pk}"] = scope.serialize()
    request.session.pop(f"{_DATA_SCOPE_SESSION_KEY}:{course.pk}", None)
    request.session.modified = True
    default_target = reverse("courses:data", args=[course.pk])
    target = request.POST.get("next") or default_target
    prefix = f"/teacher/courses/{course.pk}/data"
    if not target.startswith(prefix):
        target = default_target
    return redirect(target)


def _teaching_rows(scope):
    sessions = list(scope.sessions().select_related("student", "version"))
    attempts = list(
        scope.attempts().select_related(
            "session__student", "assignment__snapshot", "assignment__item_instance"
        )
    )
    return sessions, attempts, build_teaching_roster(scope)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_teaching(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _teaching_scope_from_request(request, course)
    if not scope.is_complete:
        return _render_data_center(
            request,
            "teacher/data_center/teaching.html",
            course,
            scope,
            active="teaching",
            blocked_message="请先选择实验和班级",
            student_summaries=[],
        )
    sessions, attempts, student_summaries = _teaching_rows(scope)
    query = request.GET.get("q", "").strip()
    if query:
        normalized_query = query.casefold()
        student_summaries = [
            row
            for row in student_summaries
            if normalized_query in (row["student"].student_number or "").casefold()
            or normalized_query in row["student"].shown_name.casefold()
        ]
    roster_student_ids = {
        enrollment.student_id for enrollment in scope.roster().only("student_id")
    }
    selected_student_id = request.GET.get("student_id")
    if selected_student_id:
        try:
            selected_student_id = int(selected_student_id)
        except (TypeError, ValueError) as error:
            raise Http404("学生不属于当前教学范围") from error
        if selected_student_id not in roster_student_ids:
            raise Http404("学生不属于当前教学范围")
    session_rows = []
    if selected_student_id:
        for session in sessions:
            if session.student_id != selected_student_id:
                continue
            current_attempts = [attempt for attempt in attempts if attempt.session_id == session.pk]
            session_rows.append(
                {
                    "session": session,
                    "attempt_count": len(current_attempts),
                    "submitted_count": sum(
                        attempt.status == Attempt.Status.SUBMITTED
                        for attempt in current_attempts
                    ),
                }
            )
    attempt_rows = []
    selected_session_id = request.GET.get("session_id")
    if selected_session_id:
        try:
            selected_session_id = int(selected_session_id)
        except (TypeError, ValueError) as error:
            raise Http404("会话不属于当前教学范围") from error
        allowed_session_ids = {row["session"].pk for row in session_rows}
        if selected_session_id not in allowed_session_ids:
            raise Http404("会话不属于当前教学范围")
        for attempt in attempts:
            if attempt.session_id != selected_session_id:
                continue
            attempt_rows.append(
                {
                    "attempt": attempt,
                    "grade": effective_grade_for_attempt(attempt),
                    "timing": summarize_attempt_timing(attempt),
                    "timeline": attempt_timeline(attempt),
                }
            )
    return _render_data_center(
        request,
        "teacher/data_center/teaching.html",
        course,
        scope,
        active="teaching",
        blocked_message="",
        student_summaries=student_summaries,
        student_search=query,
        total_roster_count=len(roster_student_ids),
        selected_student_id=selected_student_id,
        selected_session_id=selected_session_id,
        session_rows=session_rows,
        attempt_rows=attempt_rows,
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_exports(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _scope_from_request(request, course)
    teaching_scope = _teaching_scope_from_request(request, course)
    packages = [
        ("teaching", "教学成绩（XLSX）", "当前有效成绩与导出血缘"),
        ("complete", "完整研究包", "全过程数据与可复现派生"),
        ("process", "作答过程", "原始事件、响应快照与稳定输入观测"),
        ("kt", "KT", "知识追踪所需数据"),
        ("cd", "CD", "认知诊断所需数据"),
    ]
    selected_experiment = (
        course.experiments.filter(pk=scope.experiment_id).first()
        if scope.experiment_id
        else None
    )
    export_versions = (
        selected_experiment.versions.order_by("version_number")
        if selected_experiment is not None
        else []
    )
    export_version_base_values = [
        (key, value)
        for key, value in scope.serialize().items()
        if key not in {"version_mode", "version_id"}
    ]
    selected_export_version = (
        selected_experiment.versions.filter(pk=scope.version_id).first()
        if selected_experiment is not None and scope.is_single_version
        else None
    )
    extensions = enabled_extension_tables(selected_experiment)
    if "confidence_assignments" in extensions:
        packages.append(("confidence", "Confidence", "信心采集及校准数据"))
    if "simulation_events" in extensions:
        packages.append(("simulation", "Simulation", "仿真事件和快照"))
    package_rows = [
        {
            "key": key,
            "label": label,
            "description": description,
            "blocked": key in {"teaching", "kt", "cd"} and not scope.is_single_version,
        }
        for key, label, description in packages
    ]
    selected_key = request.GET.get("package", "")
    selected_package = next(
        (row for row in package_rows if row["key"] == selected_key), None
    )
    return _render_data_center(
        request,
        "teacher/data_center/exports.html",
        course,
        scope,
        active="exports",
        teaching_scope=teaching_scope,
        research_scope=True,
        scope_display=_teaching_scope_display(teaching_scope),
        packages=package_rows,
        selected_package=selected_package,
        export_versions=export_versions,
        export_version_base_values=export_version_base_values,
        export_version_base_query=urlencode(export_version_base_values),
        selected_export_version=selected_export_version,
        export_scope_display=_scope_display(scope),
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_quality(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _scope_from_request(request, course)
    teaching_scope = _teaching_scope_from_request(request, course)
    report = build_quality_report(scope)
    return _render_data_center(
        request,
        "teacher/data_center/quality.html",
        course,
        scope,
        active="quality",
        teaching_scope=teaching_scope,
        research_scope=True,
        report=report,
        states=EvaluationState,
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_export_history(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _teaching_scope_from_request(request, course)
    return _render_data_center(
        request,
        "teacher/data_center/export_history.html",
        course,
        scope,
        active="history",
        export_jobs=course.export_jobs.select_related("experiment", "snapshot", "created_by").order_by("-created_at")[:100],
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_advanced(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _scope_from_request(request, course)
    teaching_scope = _teaching_scope_from_request(request, course)
    selected_table = request.GET.get("table", "attempts")
    selected_experiment = None
    if scope.experiment_id:
        selected_experiment = course.experiments.filter(pk=scope.experiment_id).first()
    advanced_groups, selected_table, selected_table_info = build_advanced_catalog(
        experiment=selected_experiment,
        selected_table=selected_table,
    )
    preview = None
    if selected_experiment is not None:
        preview = preview_export_table(
            course=course,
            experiment=selected_experiment,
            table=selected_table,
            page=request.GET.get("page", 1),
            scope=scope,
        )
        preview["columns"] = [
            {
                "name": header,
                "description": TABLE_FIELD_DESCRIPTIONS.get(selected_table, {}).get(
                    header, COMMON_FIELD_DESCRIPTIONS.get(header, "数据字段")
                ),
            }
            for header in preview["headers"]
        ]
    return _render_data_center(
        request,
        "teacher/data_center/advanced.html",
        course,
        scope,
        active="advanced",
        teaching_scope=teaching_scope,
        research_scope=True,
        selected_table=selected_table,
        selected_table_label=TABLE_LABELS.get(selected_table, selected_table),
        selected_table_info=selected_table_info,
        selected_experiment=selected_experiment,
        preview=preview,
        advanced_groups=advanced_groups,
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_quality(request, course_pk):
    return course_data_quality(request, course_pk)


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
