from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.audit.services import log_action

from .course_access import (
    accessible_courses,
    get_accessible_course,
    lock_course,
    require_course_owner,
    require_writable_course,
)
from .forms import CourseForm, CourseSettingsForm
from .models import Course, User
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
def course_data(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    experiments = course.experiments.annotate(
        version_count=Count("versions", distinct=True),
        session_count=Count("versions__sessions", distinct=True),
    )
    return render(
        request,
        "teacher/course_data.html",
        {"course": course, "current_course": course, "experiments": experiments},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_settings(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_owner(course, request.user)
    if request.method == "POST":
        require_writable_course(course)
    form = CourseSettingsForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        previous_teacher_ids = set(course.teacher_links.values_list("teacher_id", flat=True))
        course = form.save()
        teacher_ids = set(form.cleaned_data["teachers"].values_list("pk", flat=True))
        log_action(
            request,
            "course.update",
            course,
            {"teacher_ids": sorted(teacher_ids)},
        )
        if teacher_ids != previous_teacher_ids:
            log_action(
                request,
                "course.collaborators_update",
                course,
                {
                    "added_teacher_ids": sorted(teacher_ids - previous_teacher_ids),
                    "removed_teacher_ids": sorted(previous_teacher_ids - teacher_ids),
                },
            )
        messages.success(request, "课程设置已保存。")
        return redirect("courses:settings", course.pk)
    return render(
        request,
        "teacher/course_settings.html",
        {"course": course, "current_course": course, "form": form, "is_course_owner": True},
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def course_archive(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_owner(course, request.user)
    with transaction.atomic():
        course = lock_course(course)
        require_course_owner(course, request.user)
        if course.status != Course.Status.ARCHIVED:
            course.status = Course.Status.ARCHIVED
            course.save(update_fields=["status", "updated_at"])
            log_action(request, "course.archive", course)
            messages.success(request, "课程已归档并切换为只读。")
    return redirect("courses:detail", course.pk)
