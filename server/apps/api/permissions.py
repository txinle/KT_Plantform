from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class RolePermission(BasePermission):
    message = "当前账号角色不能访问该资源。"

    def has_permission(self, request, view):
        user = request.user
        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return False
        if user.is_superuser or user.role == User.Role.ADMIN:
            return True
        return user.role in tuple(getattr(view, "allowed_roles", ()))


class CourseManagerPermission(BasePermission):
    message = "当前账号不能管理该课程。"

    def has_object_permission(self, request, view, obj):
        course = obj
        if not hasattr(course, "can_manage"):
            course = getattr(obj, "course", None)
        if course is None:
            experiment = getattr(obj, "experiment", None)
            course = getattr(experiment, "course", None)
        return bool(course and course.can_manage(request.user))
