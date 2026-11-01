from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404

from .models import Course, User


def accessible_courses(user, capability=None):
    queryset = Course.objects.select_related("owner")
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return queryset.none()
    if user.is_superuser or user.role == User.Role.ADMIN:
        return queryset
    if user.role != User.Role.TEACHER:
        return queryset.none()
    links = Q(teacher_links__teacher=user)
    if capability is not None:
        links &= Q(teacher_links__permissions__contains=[capability])
    return queryset.filter(Q(owner=user) | links).distinct()


def get_accessible_course(user, course_pk, capability=None):
    return get_object_or_404(accessible_courses(user, capability=capability), pk=course_pk)


def require_course_owner(course, user):
    if user.is_active and (user.is_superuser or user.role == User.Role.ADMIN or course.owner_id == user.id):
        return course
    raise PermissionDenied


def require_course_manager(course, user, capability=None):
    if course.can_manage(user, capability=capability):
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
