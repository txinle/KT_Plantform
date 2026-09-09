from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.db.models import Q
from apps.accounts.course_access import accessible_courses, lock_writable_course
from apps.accounts.permissions import role_required
from apps.api.frontend_entry import frontend_response
from apps.audit.services import log_action
from .forms import ExperimentForm, StudentAssignmentForm
from .models import Experiment
from .services import (
    assign_students,
    delete_experiment as delete_experiment_tree,
    lock_experiment_question_bank,
    publish_experiment,
    sync_experiment_questions,
)


def _owned(request, pk):
    qs = (
        Experiment.objects.all()
        if request.user.is_superuser or request.user.role == "admin"
        else Experiment.objects.filter(Q(owner=request.user) | Q(course__in=accessible_courses(request.user)))
    )
    return get_object_or_404(qs, pk=pk)

@role_required("teacher", "admin")
def experiment_list(request):
    return frontend_response()

@role_required("teacher", "admin")
def experiment_create(request):
    courses = accessible_courses(request.user).filter(status="active")
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
    form = ExperimentForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                experiment = form.save(commit=False)
                if experiment.question_bank.course_id:
                    course = lock_writable_course(experiment.question_bank.course_id)
                    if not courses.filter(pk=course.pk).exists():
                        raise ValidationError("题库不属于可管理的课程")
                    question_bank = lock_experiment_question_bank(
                        form.cleaned_data["question_bank"],
                        course,
                    )
                    experiment.course = course
                    experiment.created_by = request.user
                else:
                    question_bank = lock_experiment_question_bank(
                        form.cleaned_data["question_bank"]
                    )
                experiment.question_bank = question_bank
                experiment.owner = request.user
                experiment.save()
                form.save_quotas(experiment)
                sync_experiment_questions(experiment)
                log_action(request, "experiment.create", experiment)
        except ValidationError as exc:
            form.add_error("question_bank", exc)
        else:
            messages.success(request, "实验已创建，并已加入题库中的全部可用题目")
            if experiment.course_id:
                return redirect("courses:experiment_detail", experiment.course_id, experiment.pk)
            return redirect("experiments:detail", pk=experiment.pk)
    return frontend_response()


@role_required("teacher", "admin")
def experiment_edit(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views
        if request.method == "GET":
            return redirect("courses:experiment_edit", experiment.course_id, experiment.pk)
        return course_views.experiment_edit(request, experiment.course_id, experiment.pk)
    if request.method in {"GET", "HEAD"} and experiment.status == Experiment.Status.DRAFT:
        return frontend_response()
    if experiment.status != Experiment.Status.DRAFT:
        messages.error(request, "实验发布后不能修改配置")
        return redirect("experiments:detail", pk=experiment.pk)
    form = ExperimentForm(request.POST or None, instance=experiment, owner=request.user)
    if request.method == "POST" and form.is_valid():
        saved = False
        try:
            with transaction.atomic():
                experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
                if experiment.status != Experiment.Status.DRAFT:
                    raise ValidationError("实验发布后不能修改配置")
                original_bank_id = experiment.question_bank_id
                question_bank = lock_experiment_question_bank(
                    form.cleaned_data["question_bank"]
                )
                form = ExperimentForm(request.POST, instance=experiment, owner=request.user)
                if form.is_valid():
                    experiment = form.save(commit=False)
                    experiment.question_bank = question_bank
                    experiment.save()
                    form.save_quotas(experiment)
                    if experiment.question_bank_id != original_bank_id:
                        sync_experiment_questions(experiment)
                    log_action(request, "experiment.update", experiment)
                    saved = True
        except ValidationError as exc:
            form.add_error("question_bank", exc)
        else:
            if saved:
                messages.success(request, "实验配置已保存")
                return redirect("experiments:detail", pk=experiment.pk)
    return frontend_response()

@role_required("teacher", "admin")
def experiment_detail(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        return redirect("courses:experiment_detail", experiment.course_id, experiment.pk)
    return frontend_response()

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
            messages.success(request, f"实验已发布为 v{version.version_number}")
        except ValidationError as exc: messages.error(request, "; ".join(exc.messages))
    return redirect("experiments:detail", pk=experiment.pk)


@require_POST
@role_required("teacher", "admin")
def experiment_delete(request, pk):
    experiment = _owned(request, pk)
    if experiment.course_id:
        from . import course_views

        return course_views.experiment_delete(
            request, experiment.course_id, experiment.pk
        )
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再删除实验。")
    with transaction.atomic():
        details = delete_experiment_tree(experiment)
        log_action(request, "experiment.delete", details=details)
    messages.success(request, "实验及其发布版本、会话与作答记录已删除。")
    return redirect("experiments:list")

@role_required("teacher", "admin")
def experiment_results(request, pk):
    _owned(request, pk)
    return frontend_response()


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
