from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import quote
from urllib.parse import urlencode

from apps.audit.services import log_action

from .course_access import accessible_courses, get_accessible_course, require_writable_course
from .enrollment_services import (
    copy_class_enrollments,
    enroll_parsed_students,
    enroll_single_student,
    transfer_course_enrollment,
)
from .forms import (
    ClassGroupForm,
    ClassGroupRenameForm,
    ClassGroupUpdateForm,
    ManualStudentEnrollmentForm,
    PasswordResetForm,
    StudentBatchUploadForm,
    StudentClassCopyForm,
    class_group_state_token_matches,
)
from .models import ClassGroup, CourseEnrollment, User
from .permissions import role_required
from .student_import import (
    ParsedStudent,
    StudentImportError,
    build_student_import_workbook,
    parse_student_workbook,
)


def _inactive_target_class_response(request, course, class_group, action):
    messages.error(request, f"目标班级已停用，不能{action}。")
    return redirect("courses:class_detail", course.pk, class_group.pk)


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
    query = request.GET.get("q", "").strip()
    classes = course.classes.all()
    if query:
        classes = classes.filter(Q(name__icontains=query) | Q(academic_year__icontains=query))
    return render(
        request,
        "teacher/course_class_list.html",
        {
            "course": course,
            "current_course": course,
            "classes": classes,
            "form": form,
            "query": query,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_rename(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    form = ClassGroupRenameForm(request.POST, instance=class_group)
    if form.is_valid():
        form.save()
        log_action(
            request,
            "class.rename",
            class_group,
            {"course_id": course.pk, "changed_fields": ["name"]},
        )
        messages.success(request, "班级名称已更新。")
    else:
        errors = form.non_field_errors() or form.errors.get("name", [])
        messages.error(request, "班级名称更新失败：" + "; ".join(errors))
    target = reverse("courses:classes", args=[course.pk])
    query = request.POST.get("q", "").strip()
    if query:
        target = f"{target}?{urlencode({'q': query})}"
    return redirect(target)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_edit(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    form = ClassGroupUpdateForm(instance=class_group)
    saved_class_group = None
    if request.method == "POST":
        try:
            with transaction.atomic():
                class_group = get_object_or_404(
                    ClassGroup.objects.select_for_update(),
                    pk=class_pk,
                    course_id=course.pk,
                )
                form = ClassGroupUpdateForm(request.POST, instance=class_group)
                previous_values = {
                    "name": class_group.name,
                    "academic_year": class_group.academic_year,
                    "is_active": class_group.is_active,
                }
                if not class_group_state_token_matches(
                    request.POST.get("state_token", ""),
                    class_group,
                ):
                    form.add_error(None, "班级信息已被其他操作更新，请刷新后重试。")
                elif form.is_valid():
                    changed_fields = [
                        field
                        for field, previous_value in previous_values.items()
                        if form.cleaned_data[field] != previous_value
                    ]
                    saved_class_group = form.save()
                    log_action(
                        request,
                        "class.update",
                        saved_class_group,
                        {"course_id": course.pk, "changed_fields": changed_fields},
                    )
        except IntegrityError:
            form.add_error(None, "同一课程和学年中已存在该班级名称。")
        if saved_class_group:
            messages.success(request, "班级设置已保存。")
            return redirect("courses:class_detail", course.pk, saved_class_group.pk)
    return render(
        request,
        "teacher/course_class_edit.html",
        {
            "course": course,
            "current_course": course,
            "class_group": class_group,
            "form": form,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_add(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    return render(
        request,
        "teacher/course_student_add.html",
        {"course": course, "current_course": course, "class_group": class_group},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_add_manual(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if request.method == "POST":
        require_writable_course(course)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    form = ManualStudentEnrollmentForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                student, created_account = enroll_single_student(
                    course,
                    class_group,
                    form.cleaned_data["student_number"],
                    form.cleaned_data["display_name"],
                )
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            else:
                enrollment = CourseEnrollment.objects.get(course=course, student=student)
                log_action(
                    request,
                    "student.manual_enroll",
                    enrollment,
                    {
                        "course_id": course.pk,
                        "class_id": class_group.pk,
                        "created_account": created_account,
                    },
                )
                messages.success(request, "学生已加入当前班级。")
                return redirect("courses:class_detail", course.pk, class_group.pk)
    return render(
        request,
        "teacher/course_student_add_manual.html",
        {
            "course": course,
            "current_course": course,
            "class_group": class_group,
            "form": form,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_copy(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    target_class = get_object_or_404(course.classes, pk=class_pk)
    if request.method == "POST":
        require_writable_course(course)
    if not target_class.is_active:
        return _inactive_target_class_response(request, course, target_class, "复制学生")
    source_classes = ClassGroup.objects.filter(
        course__in=accessible_courses(request.user).exclude(pk=course.pk)
    ).select_related("course")
    session_key = f"course_student_copy:{course.pk}:{target_class.pk}"
    preview = None
    source_class = None
    form = StudentClassCopyForm(source_classes=source_classes)
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            source_id = request.session.get(session_key)
            source_class = source_classes.filter(pk=source_id).first()
            if source_class is None:
                messages.error(request, "复制预览已失效，请重新选择来源班级。")
            else:
                created, skipped = copy_class_enrollments(
                    target_course=course,
                    target_class=target_class,
                    source_class=source_class,
                )
                request.session.pop(session_key, None)
                log_action(
                    request,
                    "student.class_copy",
                    target_class,
                    {
                        "course_id": course.pk,
                        "source_course_id": source_class.course_id,
                        "source_class_id": source_class.pk,
                        "copied": created,
                        "skipped": skipped,
                    },
                )
                messages.success(request, f"已复制 {created} 名学生，跳过 {skipped} 名已选课学生。")
                return redirect("courses:class_detail", course.pk, target_class.pk)
        else:
            request.session.pop(session_key, None)
            form = StudentClassCopyForm(request.POST, source_classes=source_classes)
            if form.is_valid():
                source_class = form.cleaned_data["source_class"]
                request.session[session_key] = source_class.pk
                existing_ids = set(
                    CourseEnrollment.objects.filter(course=course).values_list("student_id", flat=True)
                )
                preview = [
                    {"student": enrollment.student, "skip": enrollment.student_id in existing_ids}
                    for enrollment in source_class.enrollments.filter(is_active=True).select_related("student")
                ]
    return render(
        request,
        "teacher/course_student_copy.html",
        {
            "course": course,
            "current_course": course,
            "class_group": target_class,
            "form": form,
            "source_class": source_class,
            "preview": preview,
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
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
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
    target = get_object_or_404(course.classes, pk=request.POST.get("class_group"))
    if not target.is_active:
        return _inactive_target_class_response(request, course, source, "转入学生")
    try:
        enrollment = transfer_course_enrollment(enrollment, target)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("courses:class_detail", course.pk, source.pk)
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
    if request.method == "POST":
        require_writable_course(course)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    session_key = f"course_student_import:{course.pk}:{class_group.pk}"
    preview = None
    form = StudentBatchUploadForm()

    if request.method == "POST":
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
