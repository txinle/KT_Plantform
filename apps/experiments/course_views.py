from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.course_access import (
    get_accessible_course,
    lock_writable_course,
    require_writable_course,
)
from apps.accounts.models import Course, CourseEnrollment, User
from apps.accounts.permissions import role_required
from apps.audit.services import log_action
from apps.analytics.export_fields import FIELD_GROUPS
from apps.assessment.models import ExperimentSession, StudentQuestionAssignment

from .forms import ExperimentForm, ParticipantRuleForm
from .models import Experiment
from .presentation import build_session_report_presentation
from .session_report import build_session_report_document
from .services import (
    lock_experiment_question_bank,
    delete_experiment as delete_experiment_tree,
    publish_experiment,
    save_participant_rules,
    selection_display_context,
    sync_experiment_questions,
)


def _experiment(course, pk):
    return get_object_or_404(course.experiments, pk=pk)


def _session_report_scope(request, course_pk, experiment_pk, session_pk):
    """Resolve a personal report without exposing cross-course object existence."""

    courses = Course.objects.all()
    if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
        courses = courses.filter(owner=request.user)
    course = get_object_or_404(courses, pk=course_pk)
    experiment = get_object_or_404(Experiment.objects.filter(course=course), pk=experiment_pk)
    session = get_object_or_404(
        ExperimentSession.objects.select_related("student", "version__experiment"),
        pk=session_pk,
        version__experiment=experiment,
    )
    assignments = (
        StudentQuestionAssignment.objects.filter(session=session)
        .select_related("snapshot", "attempt")
        .order_by("assigned_sequence_index")
    )
    return course, experiment, session, build_session_report_presentation(session, assignments)


def _participant_rule_initial(experiment):
    overrides = dict(
        experiment.participant_override_links.values_list("student_id", "mode")
    )
    return {
        "classes": experiment.participant_class_links.values_list("class_group_id", flat=True),
        "includes": [
            student_id
            for student_id, mode in overrides.items()
            if mode == "include"
        ],
        "excludes": [
            student_id
            for student_id, mode in overrides.items()
            if mode == "exclude"
        ],
    }


def _participant_rule_context(experiment, course):
    selected_class_ids = set(
        experiment.participant_class_links.values_list("class_group_id", flat=True)
    )
    overrides = dict(
        experiment.participant_override_links.values_list("student_id", "mode")
    )
    class_groups = list(course.classes.filter(is_active=True).order_by("academic_year", "name", "pk"))
    rows_by_class = {
        class_group.pk: {
            "class_group": class_group,
            "selected": class_group.pk in selected_class_ids,
            "students": [],
        }
        for class_group in class_groups
    }
    enrollments = (
        CourseEnrollment.objects.filter(
            course=course,
            class_group_id__in=rows_by_class,
            is_active=True,
            student__role=User.Role.STUDENT,
            student__is_active=True,
        )
        .select_related("student")
        .order_by("class_group__academic_year", "class_group__name", "student__student_number", "pk")
    )
    for enrollment in enrollments:
        rows_by_class[enrollment.class_group_id]["students"].append(
            {
                "student": enrollment.student,
                "include_checked": overrides.get(enrollment.student_id) == "include",
                "exclude_checked": overrides.get(enrollment.student_id) == "exclude",
            }
        )
    return {
        "participant_class_rows": list(rows_by_class.values()),
        "participant_rule_summary": {
            "class_count": len(selected_class_ids),
            "include_count": sum(mode == "include" for mode in overrides.values()),
            "exclude_count": sum(mode == "exclude" for mode in overrides.values()),
            "preview_count": experiment.student_links.count(),
        },
    }


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_list(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    return render(
        request,
        "teacher/course_experiment_list.html",
        {"course": course, "current_course": course, "experiments": course.experiments.all()},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if request.method == "POST":
        require_writable_course(course)
    form = ExperimentForm(request.POST or None, owner=request.user, course=course)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                course = lock_writable_course(course)
                question_bank = lock_experiment_question_bank(
                    form.cleaned_data["question_bank"],
                    course,
                )
                experiment = form.save(commit=False)
                experiment.question_bank = question_bank
                experiment.course = course
                experiment.created_by = request.user
                experiment.full_clean()
                experiment.save()
                form.save_quotas(experiment)
                sync_experiment_questions(experiment)
                log_action(request, "experiment.create", experiment, {"course_id": course.pk})
        except ValidationError as exc:
            form.add_error("question_bank", exc)
        else:
            messages.success(request, "实验草稿已创建。")
            return redirect("courses:experiment_detail", course.pk, experiment.pk)
    return render(
        request,
        "teacher/course_experiment_form.html",
        {"course": course, "current_course": course, "form": form, "experiment": None},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_detail(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    experiment = _experiment(course, pk)
    participant_form = ParticipantRuleForm(
        initial=_participant_rule_initial(experiment),
        course=course,
    )
    return render(
        request,
        "teacher/course_experiment_detail.html",
        {
            "course": course,
            "current_course": course,
            "experiment": experiment,
            "assignment_form": participant_form,
            "participant_form": participant_form,
            "student_links": experiment.student_links.select_related("student"),
            **_participant_rule_context(experiment, course),
            **selection_display_context(experiment),
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_edit(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    experiment = _experiment(course, pk)
    if experiment.status != Experiment.Status.DRAFT:
        messages.error(request, "实验发布后不能修改配置。")
        return redirect("courses:experiment_detail", course.pk, experiment.pk)
    if request.method == "POST":
        require_writable_course(course)
    form = ExperimentForm(request.POST or None, instance=experiment, owner=request.user, course=course)
    if request.method == "POST" and form.is_valid():
        saved = False
        try:
            with transaction.atomic():
                experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
                if experiment.status != Experiment.Status.DRAFT:
                    raise ValidationError("实验发布后不能修改配置。")
                course = lock_writable_course(experiment.course_id)
                original_bank_id = experiment.question_bank_id
                question_bank = lock_experiment_question_bank(
                    form.cleaned_data["question_bank"],
                    course,
                )
                form = ExperimentForm(
                    request.POST,
                    instance=experiment,
                    owner=request.user,
                    course=course,
                )
                if form.is_valid():
                    experiment = form.save(commit=False)
                    experiment.question_bank = question_bank
                    experiment.full_clean()
                    experiment.save()
                    form.save_quotas(experiment)
                    if experiment.question_bank_id != original_bank_id:
                        sync_experiment_questions(experiment)
                    log_action(request, "experiment.update", experiment, {"course_id": course.pk})
                    saved = True
        except ValidationError as exc:
            form.add_error("question_bank", exc)
        else:
            if saved:
                messages.success(request, "实验配置已保存。")
                return redirect("courses:experiment_detail", course.pk, experiment.pk)
    return render(
        request,
        "teacher/course_experiment_form.html",
        {"course": course, "current_course": course, "form": form, "experiment": experiment},
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_sync_questions(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    try:
        sync_experiment_questions(experiment)
        log_action(
            request,
            "experiment.sync_questions",
            experiment,
            {"course_id": course.pk, "count": experiment.question_links.count()},
        )
        messages.success(request, "已按当前题库重新载入全部可用题目。")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("courses:experiment_detail", course.pk, experiment.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_assign(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    form = ParticipantRuleForm(request.POST, course=course)
    if form.is_valid():
        try:
            classes = list(form.cleaned_data["classes"])
            includes = list(form.cleaned_data["includes"])
            excludes = list(form.cleaned_data["excludes"])
            save_result = save_participant_rules(experiment, classes, includes, excludes)
            log_action(
                request,
                "experiment.assign_students",
                experiment,
                {
                    "course_id": course.pk,
                    "class_count": len(classes),
                    "include_count": len(includes),
                    "exclude_count": len(excludes),
                    "preview_count": save_result.preview_count,
                },
            )
            messages.success(request, "参与班级与学生规则已保存，当前预览已更新。")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    else:
        messages.error(request, "参与班级与学生规则无效；只能选择本课程可用班级中的在读学生。")
    return redirect("courses:experiment_detail", course.pk, experiment.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_publish(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    try:
        version = publish_experiment(experiment, request.user)
        log_action(request, "experiment.publish", experiment, {"version": version.version_number})
        messages.success(request, f"实验已发布为 v{version.version_number}。")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("courses:experiment_detail", course.pk, experiment.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_delete(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再删除实验。")
    with transaction.atomic():
        details = delete_experiment_tree(experiment)
        log_action(request, "experiment.delete", details=details)
    messages.success(request, "实验及其发布版本、会话与作答记录已删除。")
    return redirect("courses:experiments", course.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_results(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    experiment = _experiment(course, pk)
    latest_version = experiment.versions.order_by("-version_number").first()
    sessions = latest_version.sessions.select_related("student") if latest_version else ExperimentSession.objects.none()
    valid_statuses = {value for value, _label in ExperimentSession.Status.choices}
    selected_status = request.GET.get("status", "")
    if selected_status in valid_statuses:
        sessions = sessions.filter(status=selected_status)
    else:
        selected_status = ""
    return render(
        request,
        "teacher/course_experiment_results.html",
        {
            "course": course,
            "current_course": course,
            "experiment": experiment,
            "sessions": sessions,
            "session_status_choices": ExperimentSession.Status.choices,
            "selected_status": selected_status,
            "export_field_groups": FIELD_GROUPS,
            "can_export_identity": (
                request.user.is_superuser
                or request.user.role == User.Role.ADMIN
                or experiment.owner_id == request.user.id
            ),
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_session_detail(request, course_pk, pk, session_pk):
    course, experiment, session, report = _session_report_scope(
        request,
        course_pk,
        pk,
        session_pk,
    )
    return render(
        request,
        "teacher/course_experiment_session_detail.html",
        {
            "course": course,
            "current_course": course,
            "experiment": experiment,
            "session": session,
            "report": report,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_session_word(request, course_pk, pk, session_pk):
    _course, experiment, session, report = _session_report_scope(
        request,
        course_pk,
        pk,
        session_pk,
    )
    document_bytes = build_session_report_document(report)
    log_action(
        request,
        "experiment.session_word_report",
        experiment,
        {
            "experiment_id": experiment.pk,
            "version": session.version.version_number,
            "session_id": session.pk,
            "student_id": session.student_id,
            "question_count": report.question_count,
        },
    )
    response = HttpResponse(
        document_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = (
        f"attachment; filename=student-session-report-{session.pk}.docx; "
        f"filename*=UTF-8''{quote(f'学生作答详情-会话-{session.pk}.docx')}"
    )
    return response
