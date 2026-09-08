from django.contrib.auth import HASH_SESSION_KEY, update_session_auth_hash
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.crypto import constant_time_compare

from apps.audit.services import log_action
from .forms import StudentPasswordChangeForm
from .models import CourseEnrollment, User
from .student_identity import StudentIdentityError, _lock_context, _require_student


def password_change_required(user):
    return bool(user.is_authenticated and user.role == User.Role.STUDENT and not user.is_superuser and user.must_change_password)


@transaction.atomic
def reset_student_initial_password(*, request, student_id, course_id=None, class_id=None):
    if course_id is not None:
        course, group = _lock_context(course_id, class_id, request)
        get_object_or_404(CourseEnrollment, course=course, class_group=group, student_id=student_id, is_active=True)
        student = get_object_or_404(User.objects.select_for_update(), pk=student_id)
    else:
        from .views import _student_queryset
        student = get_object_or_404(_student_queryset(request.user).select_for_update(of=("self",)), pk=student_id)
    _require_student(student)
    if not student.student_number:
        raise StudentIdentityError("STUDENT_NUMBER_CONFLICT", "该学生尚无学号，无法生成初始密码。")
    student.set_password(f"S{student.student_number}")
    student.must_change_password = True
    student.save(update_fields=["password", "must_change_password"])
    log_action(request, "student.password_reset", student, {"course_id": course_id, "global_account": True, "requires_password_change": True})
    return student


def change_student_password(*, request, data):
    with transaction.atomic():
        student = get_object_or_404(User.objects.select_for_update(), pk=request.user.pk)
        if not password_change_required(student):
            raise StudentIdentityError("PASSWORD_CHANGE_NOT_REQUIRED", "当前账号无需强制改密。")
        if not constant_time_compare(request.session.get(HASH_SESSION_KEY, ""), student.get_session_auth_hash()):
            raise StudentIdentityError("AUTHENTICATION_REQUIRED", "登录状态已失效，请重新登录。", status=401)
        form = StudentPasswordChangeForm(data, user=student)
        if not form.is_valid():
            raise ValidationError(form.errors.as_data())
        student.set_password(form.cleaned_data["new_password1"])
        student.must_change_password = False
        student.save(update_fields=["password", "must_change_password"])
        log_action(request, "student.password_change", student, {"forced": True})
    update_session_auth_hash(request, student)
    request.user = student
    return student
