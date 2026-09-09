from django.conf import settings
from django.contrib import messages
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.audit.services import log_action
from apps.api.frontend_entry import frontend_response

from .course_access import (
    get_accessible_course,
    lock_course,
    require_course_manager,
)
from .course_revision import course_revision_value
from .models import Course, User
from apps.assessment.models import Attempt, ExperimentSession
from apps.analytics.data_scope import DataScope, ScopeNotFound, ScopeValidationError
from apps.analytics.teaching_data import (
    attempt_timeline,
    build_teaching_roster,
    effective_grade_for_attempt,
    summarize_attempt_timing,
)
from apps.analytics.teaching_scope import TeachingScope
from apps.experiments.models import ExperimentClassSnapshot
from .permissions import role_required


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_http_methods(["GET", "HEAD"])
def course_list(request):
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_http_methods(["GET", "HEAD"])
def course_create(request):
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_http_methods(["GET", "HEAD"])
def course_detail(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_revision(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    response = JsonResponse({"revision": course_revision_value(course)})
    response["Cache-Control"] = "no-store"
    return response


# Data-center v2 ----------------------------------------------------------
# The legacy implementations above remain in the source temporarily so that
# old imports retain a stable module surface.  These definitions intentionally
# replace the public view functions with the task-oriented workbench routes.

_DATA_SCOPE_SESSION_KEY = "data_center_scope_v1"
_TEACHING_SCOPE_SESSION_KEY = "data_center_teaching_scope_v1"
_TEACHING_SCOPE_KEYS = ("experiment_id", "class_id")
_DATA_SCOPE_KEYS = (
    "scope_version",
    "experiment_ids",
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
    "search",
    "session_ref",
)
_LEGACY_SCOPE_KEYS = ("class_name", "student_number", "status")


def _saved_scope(request, course, session_prefix, scope_type):
    values = request.session.get(f"{session_prefix}:{course.pk}", {})
    if values:
        try:
            return scope_type.from_mapping(course, values)
        except (ScopeNotFound, ScopeValidationError):
            # A deleted object must not strand the user behind a saved filter,
            # or revive an older teaching selection for the same course.
            for prefix in (_DATA_SCOPE_SESSION_KEY, _TEACHING_SCOPE_SESSION_KEY):
                request.session.pop(f"{prefix}:{course.pk}", None)
    return None


def _scope_from_request(request, course):
    temporary_values = {
        key: request.GET.get(key)
        for key in (*_DATA_SCOPE_KEYS, *_LEGACY_SCOPE_KEYS)
        if request.GET.get(key) not in (None, "")
    }
    if not temporary_values or set(temporary_values) <= {"search", "session_ref", "student_id", "session_id"}:
        saved = _saved_scope(request, course, _DATA_SCOPE_SESSION_KEY, DataScope)
        if saved is None:
            teaching = _saved_scope(request, course, _TEACHING_SCOPE_SESSION_KEY, TeachingScope)
            saved = _research_scope_for_teaching(teaching) if teaching else DataScope.from_mapping(course, {})
        if not temporary_values:
            return saved
        temporary_values = {**saved.serialize(), **temporary_values}
    try:
        return DataScope.from_mapping(course, temporary_values)
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
    if not temporary_values:
        return _saved_scope(request, course, _TEACHING_SCOPE_SESSION_KEY, TeachingScope) or TeachingScope.from_mapping(course, {})
    try:
        return TeachingScope.from_mapping(course, temporary_values)
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
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_http_methods(["GET", "HEAD"])
def course_data_scope_update(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    return redirect("courses:data", course.pk)


def _teaching_rows(scope):
    sessions = list(scope.sessions().select_related("student", "version"))
    attempts = list(
        scope.attempts().select_related(
            "session__student", "assignment__snapshot", "assignment__item_instance"
        )
    )
    return sessions, attempts, build_teaching_roster(scope)


def _teaching_page_context(request, scope):
    """Build the legacy teaching-page facts for both template and API delivery."""
    if not scope.is_complete:
        return {
            "blocked_message": "请先选择实验和班级",
            "student_summaries": [],
            "student_search": "",
            "total_roster_count": 0,
            "selected_student_id": None,
            "selected_session_id": None,
            "session_rows": [],
            "attempt_rows": [],
        }
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
            current_attempts = [
                attempt for attempt in attempts if attempt.session_id == session.pk
            ]
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
    return {
        "blocked_message": "",
        "student_summaries": student_summaries,
        "student_search": query,
        "total_roster_count": len(roster_student_ids),
        "selected_student_id": selected_student_id,
        "selected_session_id": selected_session_id,
        "session_rows": session_rows,
        "attempt_rows": attempt_rows,
    }


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_teaching(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    scope = _teaching_scope_from_request(request, course)
    if settings.CLASS_CENTER_ENABLED:
        if scope.class_id is None:
            return redirect("courses:classes", course.pk)
        target = reverse(
            "courses:class_detail",
            args=[course.pk, scope.class_id],
        )
        query = "section=overview"
        if scope.experiment_id is not None:
            query = f"section=experiments&experiment_id={scope.experiment_id}"
        return redirect(f"{target}?{query}")
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_exports(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    _scope_from_request(request, course)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_quality(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    _scope_from_request(request, course)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_export_history(request, course_pk):
    get_accessible_course(request.user, course_pk)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_data_advanced(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    _scope_from_request(request, course)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_quality(request, course_pk):
    return course_data_quality(request, course_pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_http_methods(["GET", "HEAD"])
def course_settings(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_manager(course, request.user)
    return frontend_response()


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
