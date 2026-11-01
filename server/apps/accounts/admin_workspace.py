"""Platform administration projections and account mutations.

Domain-specific operations remain in the existing course, class and research services.
"""
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from .models import ClassGroup, Course, CourseEnrollment, User


def user_row(user):
    return {"id": user.pk, "name": user.shown_name, "username": user.username,
            "student_number": user.student_number, "role": user.role,
            "is_active": user.is_active, "email": user.email,
            "course_count": Course.objects.filter(Q(owner=user) | Q(teacher_links__teacher=user) |
                Q(enrollments__student=user, enrollments__is_active=True)).distinct().count()}


def course_row(course):
    from .collaboration import collaboration_token
    return {"id": course.pk, "name": course.name, "code": course.code, "term": course.term,
            "description": course.description, "status": course.status,
            "status_label": course.get_status_display(), "owner_id": course.owner_id,
            "owner_name": course.owner.shown_name, "class_count": course.classes.count(),
            "bank_count": course.question_banks.count(), "experiment_count": course.experiments.count(),
            "student_count": course.enrollments.filter(is_active=True).count(),
            "detail_url": f"/manage/courses/{course.pk}/settings/", "settings_url": f"/manage/courses/{course.pk}/settings/",
            "state_token": signing.dumps([course.pk, course.updated_at.isoformat()], salt="admin-course-update"),
            "collaboration_token": collaboration_token(course)}


def class_row(group):
    from .forms import class_group_state_token
    return {"id": group.pk, "name": group.name, "academic_year": group.academic_year,
            "course_id": group.course_id, "course_name": group.course.name if group.course else "未分配课程",
            "is_active": group.is_active, "student_count": group.enrollments.filter(is_active=True).count(),
            "owner_name": group.owner.shown_name if group.owner else "", "state_token": class_group_state_token(group),
            "manage_url": f"/api/v1/teacher/accounts/courses/{group.course_id}/classes/{group.pk}/manage/" if group.course_id else None,
            "detail_url": f"/manage/courses/?tab=classes&class_id={group.pk}"}


def audit_row(row):
    return {"id": row.pk, "actor_id": row.actor_id, "actor_name": row.actor.shown_name if row.actor else "系统",
            "actor_username": row.actor.username if row.actor else "", "action": row.action,
            "object_type": row.object_type, "object_id": row.object_id, "details": row.details,
            "ip_address": row.ip_address, "created_at": row.created_at.isoformat()}


def checked_save(obj):
    try:
        obj.full_clean()
        obj.save()
    except DjangoValidationError as exc:
        raise ValidationError(getattr(exc, "message_dict", {"detail": exc.messages})) from exc


@transaction.atomic
def create_user(request, values):
    role = values.get("role", "teacher")
    if role not in User.Role.values:
        raise ValidationError({"role": "请选择有效角色。"})
    number = str(values.get("student_number", "")).strip() if role == User.Role.STUDENT else None
    username = number if role == User.Role.STUDENT else str(values.get("username", "")).strip()
    name = str(values.get("name", "")).strip()
    if not name or not username:
        raise ValidationError("请填写姓名及用户名或学号。")
    user = User(username=username, student_number=number, display_name=name, role=role,
                email=str(values.get("email", "")).strip())
    user.set_password(f"S{username}")
    user.uses_initial_password = True
    user.initial_password_state_recorded_at = timezone.now()
    user.must_change_password = False
    checked_save(user)
    log_action(request, "admin.user.create", user, {"role": role, "username": username})
    return user


@transaction.atomic
def mutate_user(request, user_id, action, values):
    # Serialize administrator status transitions before locking the target.
    list(User.objects.select_for_update().filter(Q(role=User.Role.ADMIN) | Q(is_superuser=True)).order_by("pk"))
    user = get_object_or_404(User.objects.select_for_update(), pk=user_id)
    before = {"name": user.display_name, "username": user.username, "email": user.email, "is_active": user.is_active}
    if action == "status":
        active = values.get("is_active")
        if not isinstance(active, bool):
            raise ValidationError({"is_active": "必须明确选择启用或停用。"})
        if not active and user.pk == request.user.pk:
            raise ValidationError("不能停用当前登录账号。")
        if not active and (user.is_superuser or user.role == User.Role.ADMIN) and not User.objects.filter(
                Q(role=User.Role.ADMIN) | Q(is_superuser=True), is_active=True).exclude(pk=user.pk).exists():
            raise ValidationError("不能停用最后一个可用管理员。")
        user.is_active = active
    elif action == "update":
        user.display_name = str(values.get("name", user.display_name)).strip()
        user.email = str(values.get("email", user.email)).strip()
        if not user.display_name:
            raise ValidationError({"name": "姓名不能为空。"})
        if user.role == User.Role.STUDENT:
            # Frozen research identities remain stable; global number changes use
            # the existing explicit identity correction flow.
            if values.get("student_number", user.student_number) != user.student_number:
                raise ValidationError({"student_number": "学号是固定身份标识，不能在账号编辑中更换。"})
        else:
            user.username = str(values.get("username", user.username)).strip()
    elif action == "password":
        if values.get("confirmation") != "reset":
            raise ValidationError("请确认重置密码。")
        if user.role == User.Role.STUDENT:
            from .password_services import reset_student_initial_password
            return reset_student_initial_password(request=request, student_id=user.pk)
        user.set_password(f"S{user.username}")
        user.uses_initial_password = True
        user.initial_password_reminder_shown_at = None
        user.initial_password_state_recorded_at = timezone.now()
        user.must_change_password = False
    else:
        raise ValidationError("不支持的账号操作。")
    checked_save(user)
    after = {"name": user.display_name, "username": user.username, "email": user.email, "is_active": user.is_active}
    log_action(request, f"admin.user.{action}", user, {} if action == "password" else {"before": before, "after": after})
    return user


@transaction.atomic
def save_course(request, values, course_id=None):
    from .forms import CourseForm
    course = get_object_or_404(Course.objects.select_for_update(), pk=course_id) if course_id else Course()
    if course_id:
        if course.status != Course.Status.ACTIVE:
            raise ValidationError("归档课程需恢复后才能编辑。")
        try:
            state = signing.loads(values.get("state_token", ""), salt="admin-course-update", max_age=3600)
        except (signing.BadSignature, TypeError):
            raise ValidationError("课程信息已过期，请刷新后重试。")
        if state != [course.pk, course.updated_at.isoformat()]:
            raise ValidationError("课程已被其他操作修改，请刷新后重试。")
    else:
        course.owner = get_object_or_404(User, pk=values.get("owner_id"), role=User.Role.TEACHER, is_active=True)
    form = CourseForm(values, instance=course)
    if not form.is_valid():
        raise ValidationError(form.errors)
    course = form.save(commit=False)
    checked_save(course)
    log_action(request, "admin.course.update" if course_id else "admin.course.create", course,
               {"name": course.name, "code": course.code, "term": course.term})
    return course
