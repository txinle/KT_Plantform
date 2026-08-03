from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from apps.accounts.permissions import role_required
from apps.audit.services import log_action
from .forms import ExperimentForm, StudentAssignmentForm
from .models import Experiment
from .services import assign_students, publish_experiment, sync_experiment_questions


def _owned(request, pk):
    qs = Experiment.objects.all() if request.user.is_superuser else Experiment.objects.filter(owner=request.user)
    return get_object_or_404(qs, pk=pk)

@role_required("teacher", "admin")
def experiment_list(request):
    experiments = Experiment.objects.filter(owner=request.user)
    return render(request, "teacher/experiment_list.html", {"experiments": experiments})

@role_required("teacher", "admin")
def experiment_create(request):
    form = ExperimentForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        experiment = form.save(commit=False); experiment.owner = request.user; experiment.save()
        sync_experiment_questions(experiment)
        log_action(request, "experiment.create", experiment)
        messages.success(request, "实验已创建，并已加入题库中的全部可用题目")
        return redirect("experiments:detail", pk=experiment.pk)
    return render(request, "teacher/experiment_form.html", {"form": form})


@role_required("teacher", "admin")
def experiment_edit(request, pk):
    experiment = _owned(request, pk)
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
    assignment_form = StudentAssignmentForm(initial={"students": experiment.students.all()}, owner=request.user)
    return render(request, "teacher/experiment_detail.html", {"experiment": experiment, "assignment_form": assignment_form})

@role_required("teacher", "admin")
def experiment_assign(request, pk):
    experiment = _owned(request, pk)
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
    sessions = experiment.versions.order_by("-version_number").first().sessions.select_related("student") if experiment.versions.exists() else []
    return render(request, "teacher/experiment_results.html", {"experiment": experiment, "sessions": sessions})


@role_required("teacher", "admin")
def experiment_sync_questions(request, pk):
    experiment = _owned(request, pk)
    if request.method == "POST":
        try:
            sync_experiment_questions(experiment)
            log_action(request, "experiment.sync_questions", experiment, {"count": experiment.question_links.count()})
            messages.success(request, "已按当前题库重新载入全部可用题目")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return redirect("experiments:detail", pk=experiment.pk)
