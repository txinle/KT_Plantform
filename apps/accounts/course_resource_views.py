from django.contrib import messages
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from urllib.parse import quote

from apps.audit.services import log_action
from apps.api.frontend_entry import frontend_response

from .course_access import (
    accessible_courses,
    get_accessible_course,
    lock_writable_course,
    require_writable_course,
)
from .class_management_services import (
    CourseClassDeletionError,
    create_course_class_group,
    delete_course_class_group,
    update_course_class_group,
)
from .enrollment_services import (
    copy_class_enrollments,
    enroll_parsed_students,
    enroll_single_student,
    remove_course_enrollment,
    transfer_course_enrollment,
)
from .forms import (
    ClassGroupForm,
    ClassGroupUpdateForm,
    ClassStudentAnalyticsPolicyForm,
    ManualStudentEnrollmentForm,
    PasswordResetForm,
    StudentBatchUploadForm,
    StudentClassCopyForm,
)
from .models import ClassGroup, ClassStudentAnalyticsPolicy, CourseEnrollment, User
from .permissions import role_required
from .password_services import reset_student_initial_password
from .student_identity import StudentIdentityError
from .student_analytics_policy import update_class_student_analytics_policy
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
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
    form = ClassGroupForm(request.POST or None)
    if request.method == "POST":
        class_group = create_course_class_group(
            request=request,
            course=course,
            form=form,
        )
    else:
        class_group = None
    if class_group is not None:
        messages.success(request, "班级已创建。")
        return redirect("courses:class_detail", course.pk, class_group.pk)
    return frontend_response()


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_delete(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再删除班级。")
    class_group = get_object_or_404(course.classes, pk=class_pk)
    try:
        delete_course_class_group(
            request=request,
            course=course,
            class_group=class_group,
        )
    except CourseClassDeletionError as exc:
        return HttpResponseBadRequest("; ".join(exc.messages))
    messages.success(request, "班级已删除。")
    return redirect("courses:classes", course.pk)


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_edit(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    return frontend_response()


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_update(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    saved_class_group, form = update_course_class_group(
        request=request,
        course=course,
        class_group=class_group,
        form_data=request.POST,
    )
    if saved_class_group:
        messages.success(request, "班级设置已保存。")
        if settings.CLASS_CENTER_ENABLED:
            target = reverse(
                "courses:class_detail", args=[course.pk, saved_class_group.pk]
            )
            return redirect(f"{target}?section=manage")
        return redirect("courses:class_detail", course.pk, saved_class_group.pk)
    if settings.CLASS_CENTER_ENABLED:
        return _class_detail_response(
            request,
            course,
            class_group,
            class_settings_form=form,
            active_section_override="manage",
        )
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_add(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_add_manual(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if request.method == "POST":
        require_writable_course(course)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
    form = ManualStudentEnrollmentForm(request.POST or None)
    conflict = None
    if request.method == "POST":
        if form.is_valid():
            try:
                student, created_account = enroll_single_student(
                    course,
                    class_group,
                    form.cleaned_data["student_number"],
                    form.cleaned_data["display_name"],
                    request=request,
                    confirmation_token=request.POST.get("confirmation_token"),
                )
            except StudentIdentityError as exc:
                if exc.business_code == "STUDENT_NAME_CONFLICT":
                    conflict = exc.fields
                else:
                    form.add_error(None, "; ".join(exc.messages))
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            else:
                messages.success(request, "学生已加入当前班级。")
                return redirect("courses:class_detail", course.pk, class_group.pk)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_copy(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    target_class = get_object_or_404(course.classes, pk=class_pk)
    if request.method == "POST":
        require_writable_course(course)
    if not target_class.is_active:
        return _inactive_target_class_response(request, course, target_class, "复制学生")
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
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
    return frontend_response()


def _class_detail_response(
    request,
    course,
    class_group,
    *,
    class_settings_form=None,
    active_section_override=None,
    status=200,
):
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_detail(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    return _class_detail_response(request, course, class_group)


def _class_drilldown_scope(request, course_pk, class_pk):
    if not settings.CLASS_CENTER_ENABLED:
        raise Http404
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    return course, class_group


@require_http_methods(["GET", "POST"])
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_student_visibility(request, course_pk, class_pk):
    course, class_group = _class_drilldown_scope(request, course_pk, class_pk)
    policy = get_object_or_404(
        ClassStudentAnalyticsPolicy,
        class_group=class_group,
    )
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
    if request.method == "POST":
        form = ClassStudentAnalyticsPolicyForm(request.POST)
        if form.is_valid():
            policy = update_class_student_analytics_policy(
                class_group=class_group,
                actor=request.user,
                changes=form.cleaned_data,
            )
            messages.success(request, "学生端显示设置已更新。")
            return redirect(
                "courses:class_student_visibility",
                course.pk,
                class_group.pk,
            )
        response_status = 400
    else:
        form = ClassStudentAnalyticsPolicyForm(
            initial=ClassStudentAnalyticsPolicyForm.initial_from_policy(policy)
        )
        response_status = 200
    if course.status == course.Status.ARCHIVED:
        for field in form.fields.values():
            field.disabled = True
    response = frontend_response()
    response.status_code = response_status
    return response


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_student_detail(request, course_pk, class_pk, student_pk):
    course, class_group = _class_drilldown_scope(request, course_pk, class_pk)
    from apps.analytics.teaching.drilldown import build_student_detail

    try:
        detail = build_student_detail(class_group, student_id=student_pk)
    except LookupError as error:
        raise Http404 from error
    return frontend_response()


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_student_experiment_detail(
    request,
    course_pk,
    class_pk,
    student_pk,
    experiment_pk,
):
    course, class_group = _class_drilldown_scope(request, course_pk, class_pk)
    from apps.analytics.teaching.drilldown import build_student_experiment_detail

    try:
        detail = build_student_experiment_detail(
            class_group,
            experiment_id=experiment_pk,
            student_id=student_pk,
        )
    except LookupError as error:
        raise Http404 from error
    return frontend_response()


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_question_detail(
    request,
    course_pk,
    class_pk,
    experiment_pk,
    question_pk,
):
    course, class_group = _class_drilldown_scope(request, course_pk, class_pk)
    from apps.analytics.teaching.drilldown import build_question_detail

    try:
        detail = build_question_detail(
            class_group,
            experiment_id=experiment_pk,
            question_id=question_pk,
        )
    except LookupError as error:
        raise Http404 from error
    return frontend_response()


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_question_process(
    request,
    course_pk,
    class_pk,
    experiment_pk,
    question_pk,
    student_pk,
):
    course, class_group = _class_drilldown_scope(request, course_pk, class_pk)
    from apps.analytics.teaching.drilldown import build_attempt_process_detail

    try:
        detail = build_attempt_process_detail(
            class_group,
            experiment_id=experiment_pk,
            question_id=question_pk,
            student_id=student_pk,
        )
    except LookupError as error:
        raise Http404 from error
    return frontend_response()


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


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def remove_student(request, course_pk, class_pk, student_pk):
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
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再将学生移出课程。")

    student = enrollment.student
    try:
        with transaction.atomic():
            details = remove_course_enrollment(
                course=course,
                class_group=class_group,
                enrollment=enrollment,
            )
            log_action(request, "student.course_remove", student, details)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("courses:class_detail", course.pk, class_group.pk)

    messages.success(request, "学生已移出当前课程；全局账号和历史研究数据均已保留。")
    return redirect("courses:class_detail", course.pk, class_group.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if request.method == "POST":
        require_writable_course(course)
    if not class_group.is_active:
        return _inactive_target_class_response(request, course, class_group, "新增学生")
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
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

    return frontend_response()


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
        reset_student_initial_password(
            request=request, student_id=student_pk, course_id=course_pk, class_id=class_pk,
        )
        messages.success(request, "初始密码已重置为 S + 当前学号；学生需重新登录并修改密码。")
    else:
        messages.error(request, "请确认重置初始密码。")
    return redirect("courses:class_detail", course.pk, class_group.pk)
