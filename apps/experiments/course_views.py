from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.course_access import get_accessible_course, require_writable_course
from apps.accounts.models import User
from apps.accounts.permissions import role_required
from apps.audit.services import log_action
from apps.analytics.export_fields import FIELD_GROUPS
from apps.assessment.models import ExperimentSession

from .forms import ExperimentForm, StudentAssignmentForm
from .models import Experiment
from .services import assign_students, publish_experiment, sync_experiment_questions


def _experiment(course, pk):
    return get_object_or_404(course.experiments, pk=pk)


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
        experiment = form.save(commit=False)
        experiment.course = course
        experiment.created_by = request.user
        experiment.full_clean()
        experiment.save()
        sync_experiment_questions(experiment)
        log_action(request, "experiment.create", experiment, {"course_id": course.pk})
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
    assignment_form = StudentAssignmentForm(
        initial={"students": experiment.students.all()},
        owner=request.user,
        course=course,
    )
    return render(
        request,
        "teacher/course_experiment_detail.html",
        {
            "course": course,
            "current_course": course,
            "experiment": experiment,
            "assignment_form": assignment_form,
            "student_links": experiment.student_links.select_related("student"),
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
    original_bank_id = experiment.question_bank_id
    form = ExperimentForm(request.POST or None, instance=experiment, owner=request.user, course=course)
    if request.method == "POST" and form.is_valid():
        experiment = form.save(commit=False)
        experiment.full_clean()
        experiment.save()
        if experiment.question_bank_id != original_bank_id:
            sync_experiment_questions(experiment)
        log_action(request, "experiment.update", experiment, {"course_id": course.pk})
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
    form = StudentAssignmentForm(request.POST, owner=request.user, course=course)
    if form.is_valid():
        try:
            assign_students(experiment, form.cleaned_data["students"])
            log_action(
                request,
                "experiment.assign_students",
                experiment,
                {"count": len(form.cleaned_data["students"]), "course_id": course.pk},
            )
            messages.success(request, "学生分配已更新。")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    else:
        messages.error(request, "学生分配无效；只能选择本课程可用班级中的在读学生。")
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
        },
    )
