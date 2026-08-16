from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import quote

from apps.audit.services import log_action

from .course_access import get_accessible_course, require_writable_course
from .enrollment_services import enroll_parsed_students
from .forms import ClassGroupForm, PasswordResetForm, StudentBatchUploadForm
from .models import ClassGroup, CourseEnrollment, User
from .permissions import role_required
from .student_import import (
    ParsedStudent,
    StudentImportError,
    build_student_import_workbook,
    parse_student_workbook,
)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_list(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if request.method == "POST":
        require_writable_course(course)
    form = ClassGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        class_group = form.save(commit=False)
        class_group.course = course
        class_group.owner = course.owner
        class_group.save()
        log_action(request, "class.create", class_group, {"course_id": course.pk})
        messages.success(request, "班级已创建。")
        return redirect("courses:class_detail", course.pk, class_group.pk)
    return render(
        request,
        "teacher/course_class_list.html",
        {
            "course": course,
            "current_course": course,
            "classes": course.classes.all(),
            "form": form,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_detail(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    enrollments = class_group.enrollments.select_related("student")
    query = request.GET.get("q", "").strip()
    if query:
        enrollments = enrollments.filter(
            Q(student__student_number__icontains=query)
            | Q(student__display_name__icontains=query)
            | Q(student__username__icontains=query)
        )
    return render(
        request,
        "teacher/course_class_detail.html",
        {
            "course": course,
            "current_course": course,
            "class_group": class_group,
            "enrollments": enrollments,
            "course_classes": course.classes.filter(is_active=True),
            "query": query,
        },
    )


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import_template(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    content = build_student_import_workbook(
        [class_group],
        scope_label=f"{course.name}（{course.code} · {course.term}） / {class_group}",
    )
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    encoded_name = quote(f"{course.code}-{class_group.name}-学生导入模板.xlsx")
    response["Content-Disposition"] = (
        "attachment; filename=student_import_template.xlsx; "
        f"filename*=UTF-8''{encoded_name}"
    )
    return response


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def move_student(request, course_pk, class_pk, student_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    source = get_object_or_404(course.classes, pk=class_pk)
    enrollment = get_object_or_404(
        CourseEnrollment,
        course=course,
        class_group=source,
        student_id=student_pk,
        is_active=True,
    )
    target = get_object_or_404(course.classes, pk=request.POST.get("class_group"), is_active=True)
    enrollment.class_group = target
    enrollment.full_clean()
    enrollment.save(update_fields=["class_group"])
    log_action(
        request,
        "student.class_transfer",
        enrollment,
        {"course_id": course.pk, "from_class_id": source.pk, "to_class_id": target.pk},
    )
    messages.success(request, "学生已转入目标班级。")
    return redirect("courses:class_detail", course.pk, target.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    session_key = f"course_student_import:{course.pk}:{class_group.pk}"
    preview = None
    form = StudentBatchUploadForm()

    if request.method == "POST":
        require_writable_course(course)
        if request.POST.get("action") == "confirm":
            preview = [ParsedStudent(**item) for item in request.session.get(session_key, [])]
            try:
                if not preview:
                    raise StudentImportError("导入预览已失效，请重新上传文件。")
                created_accounts, enrollments = enroll_parsed_students(
                    course,
                    class_group,
                    preview,
                )
            except (StudentImportError, ValidationError) as exc:
                messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            else:
                request.session.pop(session_key, None)
                log_action(
                    request,
                    "student.batch_import",
                    class_group,
                    {
                        "course_id": course.pk,
                        "class_id": class_group.pk,
                        "enrollments": enrollments,
                        "created_accounts": created_accounts,
                    },
                )
                messages.success(
                    request,
                    f"已建立 {enrollments} 条课程选课，其中新建 {created_accounts} 个全局学生账号。",
                )
                return redirect("courses:class_detail", course.pk, class_group.pk)
        else:
            request.session.pop(session_key, None)
            form = StudentBatchUploadForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    preview = parse_student_workbook(
                        form.cleaned_data["file"],
                        [class_group],
                        allow_existing=True,
                    )
                except StudentImportError as exc:
                    form.add_error("file", str(exc))
                else:
                    request.session[session_key] = [item.__dict__ for item in preview]

    return render(
        request,
        "teacher/course_student_import.html",
        {
            "course": course,
            "current_course": course,
            "class_group": class_group,
            "form": form,
            "preview": preview,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def reset_student_password(request, course_pk, class_pk, student_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    enrollment = get_object_or_404(
        CourseEnrollment.objects.select_related("student"),
        course=course,
        class_group=class_group,
        student_id=student_pk,
        is_active=True,
    )
    form = PasswordResetForm(request.POST)
    if form.is_valid():
        enrollment.student.set_password(form.cleaned_data["password"])
        enrollment.student.save(update_fields=["password"])
        log_action(
            request,
            "student.password_reset",
            enrollment.student,
            {"course_id": course.pk, "global_account": True},
        )
        messages.success(request, "全局登录密码已重置；课程关系和实验分配未改变。")
    else:
        messages.error(request, "密码至少需要 8 个字符。")
    return redirect("courses:class_detail", course.pk, class_group.pk)
