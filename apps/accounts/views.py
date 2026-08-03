from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ClassGroupForm, PasswordResetForm, StudentCreateForm
from .models import ClassGroup, User
from .permissions import role_required
from apps.audit.services import log_action


@login_required
def home(request):
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        return redirect("admin:index")
    if request.user.role == User.Role.TEACHER:
        return redirect("question_bank:dashboard")
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
    return render(request, "teacher/student_list.html", {"students": _student_queryset(request.user), "form": form})


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
