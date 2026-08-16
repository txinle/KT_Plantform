from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from apps.accounts.course_access import accessible_courses
from apps.accounts.permissions import role_required
from apps.assessment.models import ExperimentSession
from apps.audit.services import log_action
from apps.analytics.export_fields import FIELD_GROUPS
from .forms import ExperimentForm, StudentAssignmentForm
from .models import Experiment
from .services import assign_students, publish_experiment, sync_experiment_questions


def _owned(request, pk):
    qs = (
        Experiment.objects.all()
        if request.user.is_superuser or request.user.role == "admin"
        else Experiment.objects.filter(Q(owner=request.user) | Q(course__in=accessible_courses(request.user)))
    )
    return get_object_or_404(qs, pk=pk)

@role_required("teacher", "admin")
def experiment_list(request):
    if accessible_courses(request.user).exists():
        return redirect("courses:list")
    experiments = Experiment.objects.filter(owner=request.user)
    return render(request, "teacher/experiment_list.html", {"experiments": experiments})

@role_required("teacher", "admin")
def experiment_create(request):
    courses = accessible_courses(request.user).filter(status="active")
    if request.method == "GET" and courses.exists():
        return redirect("courses:list")
    form = ExperimentForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        experiment = form.save(commit=False)
        if experiment.question_bank.course_id:
            if not courses.filter(pk=experiment.question_bank.course_id).exists():
                raise ValidationError("题库不属于可管理的课程")
            experiment.course = experiment.question_bank.course
            experiment.created_by = request.user
        experiment.owner = request.user; experiment.save()
        sync_experiment_questions(experiment)
        log_action(request, "experiment.create", experiment)
        messages.success(request, "实验已创建，并已加入题库中的全部可用题目")
        if experiment.course_id:
            return redirect("courses:experiment_detail", experiment.course_id, experiment.pk)
        return redirect("experiments:detail", pk=experiment.pk)
    return render(request, "teacher/experiment_form.html", {"form": form})


@role_required("teacher", "admin")
def experiment_edit(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views
        if request.method == "GET":
            return redirect("courses:experiment_edit", experiment.course_id, experiment.pk)
        return course_views.experiment_edit(request, experiment.course_id, experiment.pk)
    if experiment.status != Experiment.Status.DRAFT:
        messages.error(request, "实验发布后不能修改配置")
        return redirect("experiments:detail", pk=experiment.pk)
    original_bank_id = experiment.question_bank_id
    form = ExperimentForm(request.POST or None, instance=experiment, owner=request.user)
    if request.method == "POST" and form.is_valid():
        experiment = form.save()
        if experiment.question_bank_id != original_bank_id:
            sync_experiment_questions(experiment)
        log_action(request, "experiment.update", experiment)
        messages.success(request, "实验配置已保存")
        return redirect("experiments:detail", pk=experiment.pk)
    return render(request, "teacher/experiment_form.html", {"form": form, "experiment": experiment})

@role_required("teacher", "admin")
def experiment_detail(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        return redirect("courses:experiment_detail", experiment.course_id, experiment.pk)
    assignment_form = StudentAssignmentForm(initial={"students": experiment.students.all()}, owner=request.user)
    return render(request, "teacher/experiment_detail.html", {"experiment": experiment, "assignment_form": assignment_form})

@role_required("teacher", "admin")
def experiment_assign(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views
        return course_views.experiment_assign(request, experiment.course_id, experiment.pk)
    form = StudentAssignmentForm(request.POST, owner=request.user)
    if form.is_valid():
        try:
            assign_students(experiment, form.cleaned_data["students"])
            log_action(request, "experiment.assign_students", experiment, {"count": len(form.cleaned_data["students"])})
            messages.success(request, "学生分配已更新")
        except ValidationError as exc: messages.error(request, str(exc))
    return redirect("experiments:detail", pk=experiment.pk)

@role_required("teacher", "admin")
def experiment_publish(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views
        return course_views.experiment_publish(request, experiment.course_id, experiment.pk)
    if request.method == "POST":
        try:
            version = publish_experiment(experiment, request.user)
            log_action(request, "experiment.publish", experiment, {"version": version.version_number})
            messages.success(request, f"实验已发布为 v{version.version_number}")
        except ValidationError as exc: messages.error(request, "; ".join(exc.messages))
    return redirect("experiments:detail", pk=experiment.pk)

@role_required("teacher", "admin")
def experiment_results(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        return redirect("courses:experiment_results", experiment.course_id, experiment.pk)
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
        "teacher/experiment_results.html",
        {
            "experiment": experiment,
            "sessions": sessions,
            "session_status_choices": ExperimentSession.Status.choices,
            "selected_status": selected_status,
            "export_field_groups": FIELD_GROUPS,
        },
    )


@role_required("teacher", "admin")
def experiment_sync_questions(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views
        return course_views.experiment_sync_questions(request, experiment.course_id, experiment.pk)
    if request.method == "POST":
        try:
            sync_experiment_questions(experiment)
            log_action(request, "experiment.sync_questions", experiment, {"count": experiment.question_links.count()})
            messages.success(request, "已按当前题库重新载入全部可用题目")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return redirect("experiments:detail", pk=experiment.pk)
