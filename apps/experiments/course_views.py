import csv
from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.accounts.course_access import (
    get_accessible_course,
    lock_writable_course,
    require_writable_course,
)
from apps.accounts.models import Course, CourseEnrollment, User
from apps.accounts.permissions import role_required
from apps.api.rollout import is_vue_module_enabled
from apps.api.shell import render_vue_shell
from apps.audit.services import log_action
from apps.analytics.export_fields import FIELD_GROUPS
from apps.assessment.models import ExperimentSession, StudentQuestionAssignment

from .forms import ExperimentForm, ParticipantRuleForm
from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentVersionParticipantSnapshot,
    PublishPlan,
    PublishPreviewParticipant,
)
from .publication_services import (
    PublicationConflict,
    commit_publish_plan,
    effective_plan_schedule,
    generate_preview,
    get_or_create_publish_plan,
    publish_plan_setup_projection,
    save_plan_schedules,
    save_plan_selection,
)
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


def _is_class_experiment_task_unique_conflict(error):
    message = str(error)
    return (
        "unique_class_experiment_task" in message
        or "experiments_classexperimenttask.class_group_id, "
        "experiments_classexperimenttask.experiment_id" in message
    )


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
    if is_vue_module_enabled("teacher_experiments", request.user, course=course):
        return render_vue_shell(
            request,
            module="teacher-experiments",
            title=f"{course.name}实验 | KT Data Lab",
            course=course,
        )
    return render(
        request,
        "teacher/course_experiment_list.html",
        {"course": course, "current_course": course, "experiments": course.experiments.all()},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if request.method == "GET" and is_vue_module_enabled(
        "teacher_experiments", request.user, course=course
    ):
        return render_vue_shell(
            request,
            module="teacher-experiments",
            title=f"创建实验 | {course.name} | KT Data Lab",
            course=course,
        )
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
    if is_vue_module_enabled("teacher_experiments", request.user, course=course):
        return render_vue_shell(
            request,
            module="teacher-experiments",
            title=f"{experiment.name} | {course.name} | KT Data Lab",
            course=course,
        )
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
    if request.method == "GET" and is_vue_module_enabled(
        "teacher_experiments", request.user, course=course
    ):
        return render_vue_shell(
            request,
            module="teacher-experiments",
            title=f"编辑 {experiment.name} | {course.name} | KT Data Lab",
            course=course,
        )
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
        messages.success(request, f"实验已发布为 v{version.version_number}。")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    except IntegrityError as exc:
        if not _is_class_experiment_task_unique_conflict(exc):
            raise
        messages.error(request, "该班级已存在此实验的正式任务；请新建或克隆实验后再发布。")
    return redirect("courses:experiment_detail", course.pk, experiment.pk)


def _publish_plan_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValidationError("开放时间格式无效。")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, ZoneInfo("Asia/Shanghai"))
    return parsed


def _publish_plan_context(course, experiment, plan):
    selected = {
        entry.class_group_id: entry
        for entry in plan.class_plans.prefetch_related("exclusions").select_related("class_group")
    }
    task_class_ids = set(
        ClassExperimentTask.objects.filter(experiment=experiment).values_list(
            "class_group_id", flat=True
        )
    )
    rows = []
    for class_group in course.classes.filter(is_active=True).order_by(
        "academic_year", "name", "pk"
    ):
        entry = selected.get(class_group.pk)
        students = list(
            CourseEnrollment.objects.filter(
                course=course,
                class_group=class_group,
                is_active=True,
                student__role=User.Role.STUDENT,
                student__is_active=True,
            )
            .select_related("student")
            .order_by("student__student_number", "student_id")
        )
        exclusions = set(entry.exclusions.values_list("student_id", flat=True)) if entry else set()
        conflicts = list(
            ExperimentVersionParticipantSnapshot.objects.filter(
                version__experiment=experiment,
                class_task__isnull=False,
                student_id__in=[enrollment.student_id for enrollment in students],
            )
            .exclude(class_task__class_group=class_group)
            .select_related("student", "class_task__class_group")
            .order_by("student__student_number")
        )
        conflicts = [
            conflict
            for conflict in conflicts
            if conflict.student_id not in exclusions
        ]
        opens_at, closes_at = effective_plan_schedule(entry) if entry else (None, None)
        rows.append(
            {
                "class_group": class_group,
                "entry": entry,
                "students": students,
                "exclusions": exclusions,
                "conflicts": conflicts,
                "conflict_count": len(conflicts),
                "final_participant_count": len(students) - len(exclusions),
                "already_published": class_group.pk in task_class_ids,
                "opens_at": opens_at,
                "closes_at": closes_at,
            }
        )
    preview = plan.preview_run
    preview_results = ()
    if preview is not None:
        preview_results = preview.class_results.select_related(
            "publish_plan_class__class_group", "randomization_identity"
        ).prefetch_related("participants__items__source_question").order_by("pk")
    return {
        "course": course,
        "current_course": course,
        "experiment": experiment,
        "plan": plan,
        "plan_class_rows": rows,
        "preview": preview,
        "preview_results": preview_results,
        "published_task_count": ClassExperimentTask.objects.filter(experiment=experiment).count(),
        "task_count": course.classes.filter(is_active=True).count(),
        "selected_conflict_count": sum(
            row["conflict_count"]
            for row in rows
            if row["entry"] is not None
        ),
        "setup": publish_plan_setup_projection(plan),
    }


def _preview_participants_context(request, plan):
    preview = plan.preview_run
    if preview is None:
        return {}
    query = request.GET.get("q", "").strip()
    only_anomalies = request.GET.get("only_anomalies") == "1"
    participants = PublishPreviewParticipant.objects.filter(
        class_result__preview_run=preview
    ).select_related(
        "student",
        "class_result__publish_plan_class__class_group",
    ).prefetch_related("items__source_question").order_by(
        "student__student_number", "student_id", "pk"
    )
    if query:
        participants = participants.filter(
            Q(student__student_number__icontains=query)
            | Q(student__username__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
        )
    if only_anomalies:
        participants = participants.filter(is_valid=False)
    paginator = Paginator(participants, 20)
    return {
        "preview_page": paginator.get_page(request.GET.get("page", 1)),
        "preview_query": query,
        "preview_only_anomalies": only_anomalies,
        "preview_total_count": participants.count(),
        "preview_anomaly_count": PublishPreviewParticipant.objects.filter(
            class_result__preview_run=preview,
            is_valid=False,
        ).count(),
    }


def _preview_export_response(plan):
    preview = plan.preview_run
    if preview is None:
        return HttpResponseBadRequest("当前发布计划没有有效预览。")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="publish-preview-{preview.pk}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["班级", "学号", "姓名", "题数", "题目顺序", "校验"])
    participants = (
        PublishPreviewParticipant.objects.filter(class_result__preview_run=preview)
        .select_related("student", "class_result__publish_plan_class__class_group")
        .prefetch_related("items__source_question")
        .order_by("student__student_number", "student_id", "pk")
    )
    for participant in participants:
        items = participant.items.all().order_by("assigned_sequence_index")
        writer.writerow(
            [
                participant.class_result.publish_plan_class.class_group,
                participant.student.student_number,
                participant.student.shown_name,
                participant.item_count,
                " → ".join(item.source_question.code for item in items),
                "通过" if participant.is_valid else "异常",
            ]
        )
    return response


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_publish_plan_start(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    try:
        plan = get_or_create_publish_plan(experiment, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("courses:experiment_detail", course.pk, experiment.pk)
    log_action(
        request,
        "experiment.publish_plan_open",
        experiment,
        {"course_id": course.pk, "publish_plan_id": plan.pk, "plan_revision": plan.plan_revision},
    )
    return redirect("courses:experiment_publish_plan", course.pk, experiment.pk, plan.pk, 1)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_publish_plan(request, course_pk, pk, plan_pk, step):
    course = get_accessible_course(request.user, course_pk)
    experiment = _experiment(course, pk)
    plan = get_object_or_404(PublishPlan.objects.select_related("experiment", "preview_run"), pk=plan_pk, experiment=experiment)
    if step not in {1, 2}:
        return redirect("courses:experiment_publish_plan", course.pk, experiment.pk, plan.pk, 1)
    if request.method == "GET" and step == 2 and request.GET.get("preview_export") == "1":
        return _preview_export_response(plan)
    if request.method == "GET" and is_vue_module_enabled(
        "teacher_experiments", request.user, course=course
    ):
        return render_vue_shell(
            request,
            module="teacher-experiments",
            title=f"发布 {experiment.name} | {course.name} | KT Data Lab",
            course=course,
        )
    if request.method == "POST":
        require_writable_course(course)
        try:
            expected_revision = int(request.POST.get("plan_revision", ""))
            if step == 1:
                action = request.POST.get("action")
                if action == "save_selection":
                    class_ids = request.POST.getlist("classes")
                    exclusions = {
                        int(class_id): request.POST.getlist(f"exclude_{class_id}")
                        for class_id in class_ids
                    }
                    plan = save_plan_selection(
                        plan,
                        request.user,
                        expected_revision=expected_revision,
                        class_ids=class_ids,
                        exclusions_by_class=exclusions,
                    )
                    log_action(
                        request,
                        "experiment.publish_plan_selection",
                        experiment,
                        {"publish_plan_id": plan.pk, "plan_revision": plan.plan_revision, "class_count": len(class_ids)},
                    )
                    messages.success(request, "班级与参与者草稿已保存。")
                elif action == "save_schedule":
                    overrides = {}
                    for entry in plan.class_plans.all():
                        if request.POST.get(f"schedule_mode_{entry.class_group_id}") == "override":
                            overrides[entry.class_group_id] = (
                                _publish_plan_datetime(request.POST.get(f"opens_{entry.class_group_id}")),
                                _publish_plan_datetime(request.POST.get(f"closes_{entry.class_group_id}")),
                            )
                    plan = save_plan_schedules(
                        plan,
                        request.user,
                        expected_revision=expected_revision,
                        default_opens_at=_publish_plan_datetime(request.POST.get("default_opens_at")),
                        default_closes_at=_publish_plan_datetime(request.POST.get("default_closes_at")),
                        overrides=overrides,
                    )
                    log_action(
                        request,
                        "experiment.publish_plan_schedule",
                        experiment,
                        {"publish_plan_id": plan.pk, "plan_revision": plan.plan_revision, "override_count": len(overrides)},
                    )
                    messages.success(request, "开放排期草稿已保存。")
                elif action == "preview":
                    preview = generate_preview(plan, request.user, expected_revision=expected_revision)
                    log_action(
                        request,
                        "experiment.publish_plan_preview",
                        experiment,
                        {"publish_plan_id": plan.pk, "preview_run_id": preview.pk, "plan_revision": preview.plan_revision, "output_hash": preview.output_hash},
                    )
                    return redirect("courses:experiment_publish_plan", course.pk, experiment.pk, plan.pk, 2)
                else:
                    raise ValidationError("未知的发布计划操作。")
                return redirect("courses:experiment_publish_plan", course.pk, experiment.pk, plan.pk, 1)
            else:
                tasks = commit_publish_plan(plan, request.user, expected_revision=expected_revision)
                log_action(
                    request,
                    "experiment.publish_plan_commit",
                    experiment,
                    {"publish_plan_id": plan.pk, "class_task_ids": [task.pk for task in tasks], "class_count": len(tasks)},
                )
                messages.success(request, f"已为 {len(tasks)} 个班级创建正式实验任务。")
                return redirect("courses:experiment_detail", course.pk, experiment.pk)
        except (ValidationError, PublicationConflict) as exc:
            messages.error(request, "; ".join(exc.messages))
            plan.refresh_from_db()
    context = {
        **_publish_plan_context(course, experiment, plan),
        "current_step": step,
    }
    if step == 2:
        context.update(_preview_participants_context(request, plan))
    return render(
        request,
        "teacher/course_experiment_publish_wizard.html",
        context,
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_delete(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    experiment = _experiment(course, pk)
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再删除实验。")
    try:
        with transaction.atomic():
            details = delete_experiment_tree(experiment)
            log_action(request, "experiment.delete", details=details)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("courses:experiment_detail", course.pk, experiment.pk)
    messages.success(request, "实验及其发布版本、会话与作答记录已删除。")
    return redirect("courses:experiments", course.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_results(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    experiment = _experiment(course, pk)
    if settings.CLASS_CENTER_ENABLED:
        tasks = tuple(
            ClassExperimentTask.objects.filter(
                experiment=experiment,
                class_group__course=course,
            )
            .select_related("class_group", "class_snapshot")
            .order_by("class_group__academic_year", "class_group__name", "pk")
        )
        analysis_available = experiment.status in {
            Experiment.Status.FROZEN,
            Experiment.Status.PUBLISHED,
            Experiment.Status.CLOSED,
        }
        if analysis_available and len(tasks) == 1:
            task = tasks[0]
            target = reverse(
                "courses:class_detail",
                args=[course.pk, task.class_group_id],
            )
            return redirect(
                f"{target}?section=experiments&experiment_id={experiment.pk}"
            )
        return render(
            request,
            "teacher/course_experiment_class_tasks.html",
            {
                "course": course,
                "current_course": course,
                "experiment": experiment,
                "tasks": tasks if analysis_available else (),
            },
        )
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
            "can_export_identity": True,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_session_detail(request, course_pk, pk, session_pk):
    if settings.CLASS_CENTER_ENABLED:
        course = get_accessible_course(request.user, course_pk)
        experiment = get_object_or_404(course.experiments, pk=pk)
        session = get_object_or_404(
            ExperimentSession.objects.select_related("class_snapshot"),
            pk=session_pk,
            version__experiment=experiment,
        )
        task = ClassExperimentTask.objects.filter(
            class_group__course=course,
            experiment=experiment,
            release_snapshot=session.version,
            class_snapshot=session.class_snapshot,
        ).select_related("class_group").first()
        if task is not None:
            return redirect(
                "courses:class_student_experiment_detail",
                course.pk,
                task.class_group_id,
                session.student_id,
                experiment.pk,
            )
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
