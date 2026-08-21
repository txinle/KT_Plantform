from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404

from .models import Course, User


def accessible_courses(user):
    queryset = Course.objects.select_related("owner")
    if user.is_superuser or user.role == User.Role.ADMIN:
        return queryset
    if user.role != User.Role.TEACHER:
        return queryset.none()
    return queryset.filter(Q(owner=user) | Q(teacher_links__teacher=user)).distinct()


def get_accessible_course(user, course_pk):
    return get_object_or_404(accessible_courses(user), pk=course_pk)


def require_course_owner(course, user):
    if user.is_superuser or user.role == User.Role.ADMIN or course.owner_id == user.id:
        return course
    raise PermissionDenied


def require_course_manager(course, user):
    if course.can_manage(user):
        return course
    raise PermissionDenied


def require_writable_course(course):
    if course.status != Course.Status.ACTIVE:
        raise PermissionDenied
    return course


def lock_course(course_or_pk):
    course_pk = getattr(course_or_pk, "pk", course_or_pk)
    return Course.objects.select_for_update().get(pk=course_pk)


def lock_writable_course(course_or_pk):
    return require_writable_course(lock_course(course_or_pk))
