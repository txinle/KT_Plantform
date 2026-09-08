import hashlib
import json

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.audit.services import log_action
from .membership_history import has_course_membership_history, has_other_course_membership_history
from .models import ClassGroup, Course, CourseEnrollment, User


CONFIRMATION_MAX_AGE = 600
CONFIRMATION_SALT = "accounts.student_identity.v1"


class StudentIdentityError(ValidationError):
    def __init__(self, code, message, *, fields=None, status=409):
        self.business_code = code
        self.fields = fields or {}
        self.status_code = status
        super().__init__(message, code=code)


def _stale():
    return StudentIdentityError("STALE_CONFIRMATION", "确认信息已失效或相关信息已变化，请刷新后重新确认。")


def _forbidden():
    return StudentIdentityError(
        "STUDENT_IDENTITY_UPDATE_FORBIDDEN",
        "该学号已存在，但登记姓名与当前输入不一致。该账号不属于本课程历史成员，当前教师无权修改其全局身份信息。请由管理员更正后再加入。",
        status=403,
    )


def affects_other_courses(student, course):
    return has_other_course_membership_history(course.pk, student.pk)


def _state(student, course, class_group):
    data = {
        "identity": [student.pk, student.student_number, student.username, student.display_name, student.role, student.is_active],
        "course": [course.pk, course.status, course.owner_id],
        "teachers": list(course.teacher_links.order_by("teacher_id").values_list("teacher_id", flat=True)),
        "class": [class_group.pk, class_group.course_id, class_group.name, class_group.academic_year, class_group.is_active],
        "memberships": list(student.course_enrollments.order_by("pk").values_list("pk", "course_id", "class_group_id", "is_active")),
        "affects_other_courses": affects_other_courses(student, course),
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _token(student, course, class_group, user, *, action, submitted=None):
    return signing.dumps({
        "actor_id": user.pk, "student_id": student.pk, "course_id": course.pk,
        "class_id": class_group.pk, "action": action, "state": _state(student, course, class_group),
        "submitted": submitted,
    }, salt=CONFIRMATION_SALT, compress=True)


def identity_state_token(student, course, class_group, user):
    return _token(student, course, class_group, user, action="edit")


def _read_token(token, request, course_id, class_id, *, action, submitted=None):
    try:
        payload = signing.loads(token, salt=CONFIRMATION_SALT, max_age=CONFIRMATION_MAX_AGE)
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise _stale() from exc
    if not isinstance(payload, dict) or any((
        payload.get("actor_id") != request.user.pk,
        payload.get("course_id") != course_id,
        payload.get("class_id") != class_id,
        payload.get("action") != action,
        payload.get("submitted") != submitted,
    )):
        raise _stale()
    return payload


def _lock_context(course_id, class_id, request, *, confirmation=False, adding=False):
    course = Course.objects.select_for_update().filter(pk=course_id).first()
    class_group = ClassGroup.objects.select_for_update().filter(pk=class_id, course_id=course_id).first()
    if (
        course is None or class_group is None or course.status != Course.Status.ACTIVE
        or (request is not None and not course.can_manage(request.user, lock_teacher_link=True))
        or (adding and not class_group.is_active)
    ):
        if confirmation:
            raise _stale()
        if class_group is not None and adding and not class_group.is_active:
            raise StudentIdentityError("STUDENT_IDENTITY_UPDATE_FORBIDDEN", "目标班级已停用，不能新增学生。", status=403)
        raise StudentIdentityError("STUDENT_IDENTITY_UPDATE_FORBIDDEN", "当前课程或班级不可操作，或你没有管理权限。", status=403)
    return course, class_group


def _require_student(student):
    if student.role != User.Role.STUDENT or student.is_superuser:
        raise StudentIdentityError("STUDENT_ACCOUNT_TYPE_CONFLICT", "该学号命中了非学生账号，不能作为学生加入课程。")


def _existing_student(number):
    matches = list(User.objects.select_for_update().filter(Q(student_number=number) | Q(username=number)).order_by("pk"))
    for student in matches:
        _require_student(student)
    if len(matches) > 1 or (matches and matches[0].student_number != number):
        raise StudentIdentityError("USERNAME_CONFLICT", "该登录用户名已被其他账号占用，请核对学号。")
    return matches[0] if matches else None


def _check_number(student, number):
    others = User.objects.exclude(pk=student.pk)
    if others.filter(student_number=number).exists():
        raise StudentIdentityError("STUDENT_NUMBER_CONFLICT", "该学号已被其他学生账号占用。")
    if others.filter(username=number).exists():
        raise StudentIdentityError("USERNAME_CONFLICT", "该登录用户名已被其他账号占用。")


def _check_enrollment(student, course, class_group):
    enrollment = CourseEnrollment.objects.filter(student=student, course=course, is_active=True).first()
    if enrollment:
        if enrollment.class_group_id == class_group.pk:
            raise StudentIdentityError("STUDENT_ALREADY_ENROLLED", "该学生已在当前班级，请使用成员列表中的编辑信息。")
        raise StudentIdentityError("STUDENT_ALREADY_IN_OTHER_CLASS", "该学生已在本课程其他班级，请使用转班功能。")


def _audit_identity(request, student, course, class_group, before):
    log_action(request, "student.identity_update", student, {
        "course_id": course.pk, "class_id": class_group.pk, "global_account": True,
        "before": before,
        "after": {"student_number": student.student_number, "display_name": student.display_name, "username": student.username},
    })


@transaction.atomic
def update_student_identity(*, request, course_id, class_id, student_id, student_number, display_name, state_token):
    payload = _read_token(state_token, request, course_id, class_id, action="edit")
    course, class_group = _lock_context(course_id, class_id, request, confirmation=True)
    student = User.objects.select_for_update().filter(pk=student_id).first()
    if (
        student is None or payload.get("student_id") != student_id
        or student.role != User.Role.STUDENT or student.is_superuser
        or payload.get("state") != _state(student, course, class_group)
        or not CourseEnrollment.objects.filter(course=course, class_group=class_group, student=student, is_active=True).exists()
    ):
        raise _stale()
    before = {"student_number": student.student_number, "display_name": student.display_name, "username": student.username}
    if student.student_number != student_number:
        _check_number(student, student_number)
        student.username = student_number
    student.student_number = student_number
    student.display_name = display_name
    try:
        with transaction.atomic():
            student.save(update_fields=["student_number", "username", "display_name"])
    except IntegrityError as exc:
        _check_number(student, student_number)
        raise StudentIdentityError("STUDENT_NUMBER_CONFLICT", "学号或登录用户名已被其他操作占用，请刷新后重试。") from exc
    _audit_identity(request, student, course, class_group, before)
    return student, course, class_group


@transaction.atomic
def enroll_student(course, class_group, student_number, display_name, *, request=None, confirmation_token=None):
    course_id, class_id = getattr(course, "pk", course), getattr(class_group, "pk", class_group)
    submitted = {"student_number": student_number.strip(), "display_name": display_name.strip()}
    payload = None
    if confirmation_token:
        payload = _read_token(confirmation_token, request, course_id, class_id, action="reenroll", submitted=submitted)
    course, class_group = _lock_context(course_id, class_id, request, confirmation=bool(payload), adding=True)
    if payload:
        student = User.objects.select_for_update().filter(pk=payload.get("student_id")).first()
        if (
            student is None or student.role != User.Role.STUDENT or student.is_superuser
            or payload.get("state") != _state(student, course, class_group)
            or not has_course_membership_history(course.pk, student.pk)
        ):
            raise _stale()
    else:
        student = _existing_student(submitted["student_number"])
    created = student is None
    if student is not None:
        _check_enrollment(student, course, class_group)
        if student.shown_name != submitted["display_name"]:
            if not has_course_membership_history(course.pk, student.pk):
                raise _forbidden()
            if not payload:
                fields = {**submitted, "existing_name": student.shown_name, "submitted_name": submitted["display_name"], "affects_other_courses": affects_other_courses(student, course)}
                if request is not None:
                    fields["confirmation_token"] = _token(student, course, class_group, request.user, action="reenroll", submitted=submitted)
                raise StudentIdentityError("STUDENT_NAME_CONFLICT", "该学号已存在，该学生曾加入本课程。请确认是否更正全局姓名并重新加入当前班级。", fields=fields)
            before = {"student_number": student.student_number, "display_name": student.display_name, "username": student.username}
            student.display_name = submitted["display_name"]
            student.save(update_fields=["display_name"])
            _audit_identity(request, student, course, class_group, before)
    else:
        try:
            with transaction.atomic():
                student = User.objects.create_user(
                    username=submitted["student_number"], student_number=submitted["student_number"],
                    display_name=submitted["display_name"], role=User.Role.STUDENT,
                    password=f"S{submitted['student_number']}", must_change_password=True,
                )
        except IntegrityError as exc:
            raise StudentIdentityError("STUDENT_NUMBER_CONFLICT", "该学号或登录用户名已被其他操作占用，请刷新后重试。") from exc
    enrollment, _ = CourseEnrollment.objects.update_or_create(
        course=course, student=student, defaults={"class_group": class_group, "is_active": True},
    )
    if request is not None:
        log_action(request, "student.manual_enroll", enrollment, {"course_id": course.pk, "class_id": class_group.pk, "student_id": student.pk, "created_account": created})
    return student, created
