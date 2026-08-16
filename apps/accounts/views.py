from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from openpyxl import Workbook

from .forms import ClassGroupForm, PasswordResetForm, StudentBatchUploadForm, StudentCreateForm
from .models import ClassGroup, User
from .permissions import role_required
from .student_import import ParsedStudent, StudentImportError, parse_student_workbook
from apps.audit.services import log_action


class PlatformLoginView(LoginView):
    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_demo_credentials"] = settings.DEBUG
        return context


@login_required
def home(request):
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        return redirect("admin:index")
    if request.user.role == User.Role.TEACHER:
        return redirect("courses:list")
    return redirect("assessment:dashboard")


def _class_queryset(user):
    qs = ClassGroup.objects.all()
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        qs = qs.filter(owner=user)
    return qs


def _student_queryset(user):
    qs = User.objects.filter(role=User.Role.STUDENT).select_related("class_group")
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        qs = qs.filter(class_group__owner=user)
    return qs


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_list(request):
    form = ClassGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        class_group = form.save(commit=False)
        class_group.owner = request.user
        class_group.save()
        log_action(request, "class.create", class_group, {"name": class_group.name, "academic_year": class_group.academic_year})
        messages.success(request, "班级已创建")
        return redirect("accounts:class_list")
    return render(request, "teacher/class_list.html", {"classes": _class_queryset(request.user), "form": form})


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_list(request):
    form = StudentCreateForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        log_action(request, "student.create", student, {"student_number": student.student_number, "class": str(student.class_group or "")})
        messages.success(request, "学生账号已创建")
        return redirect("accounts:student_list")
    classes = _class_queryset(request.user)
    students = _student_queryset(request.user)
    query = request.GET.get("q", "").strip()
    selected_class_id = request.GET.get("class_id", "").strip()
    if query:
        students = students.filter(Q(student_number__icontains=query) | Q(display_name__icontains=query))
    if selected_class_id.isdecimal() and classes.filter(pk=selected_class_id).exists():
        students = students.filter(class_group_id=selected_class_id)
    else:
        selected_class_id = ""
    return render(
        request,
        "teacher/student_list.html",
        {
            "students": students,
            "classes": classes,
            "form": form,
            "query": query,
            "selected_class_id": selected_class_id,
            "has_filters": bool(query or selected_class_id),
        },
    )


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import_template(request):
    classes = list(_class_queryset(request.user))
    workbook = Workbook()
    import_sheet = workbook.active
    import_sheet.title = "学生导入"
    import_sheet.append(["学号", "姓名", "班级"])
    import_sheet.freeze_panes = "A2"
    for column in ("A", "B", "C"):
        import_sheet.column_dimensions[column].number_format = "@"
    instructions = workbook.create_sheet("填写说明")
    instructions.append(["填写规则"])
    instructions.append(["仅支持 .xlsx；学号、姓名、班级均为必填字段。"])
    instructions.append(["班级必须与下列可用班级完整名称精确一致。"])
    instructions.append(["初始密码规则：S + 学号。"])
    instructions.append([])
    instructions.append(["填写示例（仅供参考，不会从本页导入）"])
    instructions.append(["学号", "姓名", "班级"])
    instructions.append(["20260001", "示例学生", str(classes[0]) if classes else "2026 示例班级"])
    instructions.append([])
    instructions.append(["可用班级完整名称"])
    for class_group in classes:
        instructions.append([str(class_group)])
    stream = BytesIO()
    workbook.save(stream)
    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    encoded_name = quote("学生批量导入模板.xlsx")
    response["Content-Disposition"] = (
        f"attachment; filename=student_import_template.xlsx; filename*=UTF-8''{encoded_name}"
    )
    return response


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import(request):
    session_key = "student_batch_import"
    preview = None
    form = StudentBatchUploadForm()
    if request.method == "POST" and request.POST.get("action") == "confirm":
        stored = request.session.get(session_key, [])
        preview = [ParsedStudent(**item) for item in stored]
        try:
            with transaction.atomic():
                accessible_classes = {
                    class_group.pk: class_group for class_group in _class_queryset(request.user)
                }
                numbers = [item.student_number for item in preview]
                if not preview:
                    raise StudentImportError("导入预览已失效，请重新上传文件")
                if User.objects.filter(Q(student_number__in=numbers) | Q(username__in=numbers)).exists():
                    raise StudentImportError("部分学号已存在，请重新校验名单")
                for item in preview:
                    class_group = accessible_classes.get(item.class_id)
                    if class_group is None or str(class_group) != item.class_name:
                        raise StudentImportError(f"班级已变更或无权访问：{item.class_name}")
                    User.objects.create_user(
                        username=item.student_number,
                        student_number=item.student_number,
                        display_name=item.display_name,
                        class_group=class_group,
                        role=User.Role.STUDENT,
                        password=f"S{item.student_number}",
                    )
        except (StudentImportError, IntegrityError) as exc:
            messages.error(request, str(exc) if str(exc) else "导入失败，请重新校验名单")
        else:
            request.session.pop(session_key, None)
            log_action(request, "student.batch_import", details={"count": len(preview)})
            messages.success(request, f"已导入 {len(preview)} 名学生")
            return redirect("accounts:student_list")
    elif request.method == "POST":
        request.session.pop(session_key, None)
        form = StudentBatchUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                preview = parse_student_workbook(
                    form.cleaned_data["file"],
                    _class_queryset(request.user),
                )
            except StudentImportError as exc:
                form.add_error("file", str(exc))
            else:
                request.session[session_key] = [item.__dict__ for item in preview]
    return render(
        request,
        "teacher/student_import.html",
        {"form": form, "preview": preview},
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def reset_student_password(request, pk):
    student = get_object_or_404(_student_queryset(request.user), pk=pk)
    if request.method != "POST":
        raise PermissionDenied
    form = PasswordResetForm(request.POST)
    if form.is_valid():
        student.set_password(form.cleaned_data["password"])
        student.save(update_fields=["password"])
        log_action(request, "student.password_reset", student)
        messages.success(request, f"{student.shown_name} 的密码已重置")
    else:
        messages.error(request, "密码至少需要 8 个字符")
    return redirect("accounts:student_list")
