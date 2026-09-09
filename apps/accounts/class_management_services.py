from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404

from apps.audit.services import log_action

from .course_access import lock_writable_course, require_writable_course
from .forms import (
    ClassGroupForm,
    ClassGroupUpdateForm,
    class_group_state_token_matches,
)
from .models import ClassGroup, ClassStudentAnalyticsPolicy


CLASS_GROUP_AUDIT_FIELDS = ("name", "academic_year", "is_active")


class CourseClassDeletionError(ValidationError):
    """Raised when the legacy class deletion invariants reject a deletion."""


def can_create_course_class_group(course, user):
    """Return the legacy class-list creation capability for this course user."""
    if not course.can_manage(user):
        return False
    try:
        require_writable_course(course)
    except PermissionDenied:
        return False
    return True


def create_course_class_group(*, request, course, form):
    """Apply the legacy course class-list creation form and audit behavior."""
    require_writable_course(course)
    if not form.is_valid():
        return None
    class_group = form.save(commit=False)
    class_group.course = course
    class_group.owner = course.owner
    class_group.save()
    log_action(request, "class.create", class_group, {"course_id": course.pk})
    return class_group


def _class_group_audit_details(course, class_group, previous_values, changed_fields):
    changed_fields = [
        field for field in CLASS_GROUP_AUDIT_FIELDS if field in changed_fields
    ]
    return {
        "course_id": course.pk,
        "changed_fields": changed_fields,
        "changes": {
            field: {
                "before": previous_values[field],
                "after": getattr(class_group, field),
            }
            for field in changed_fields
        },
    }


def update_course_class_group(*, request, course, class_group, form_data):
    """Apply the legacy class update transaction for either HTML or API callers."""
    form = ClassGroupUpdateForm(form_data, instance=class_group)
    saved_class_group = None
    try:
        with transaction.atomic():
            course = lock_writable_course(course)
            class_group = get_object_or_404(
                ClassGroup.objects.select_for_update(),
                pk=class_group.pk,
                course_id=course.pk,
            )
            form = ClassGroupUpdateForm(form_data, instance=class_group)
            previous_values = {
                "name": class_group.name,
                "academic_year": class_group.academic_year,
                "is_active": class_group.is_active,
            }
            if not class_group_state_token_matches(
                form_data.get("state_token", ""), class_group
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
                    _class_group_audit_details(
                        course,
                        saved_class_group,
                        previous_values,
                        changed_fields,
                    ),
                )
    except IntegrityError:
        form.add_error(None, "同一课程和年级中已存在该班级名称。")
    return saved_class_group, form


def delete_course_class_group(*, request, course, class_group):
    """Apply the legacy protected class-deletion transaction for HTML or API callers."""
    with transaction.atomic():
        course = lock_writable_course(course)
        class_group = get_object_or_404(
            ClassGroup.objects.select_for_update(),
            pk=class_group.pk,
            course_id=course.pk,
        )
        from apps.experiments.research_deletion import CLASS_BLOCKED, class_experiments

        if class_experiments(class_group).exists():
            raise CourseClassDeletionError(CLASS_BLOCKED)
        details = {
            "class_id": class_group.pk,
            "course_id": course.pk,
            "name": class_group.name,
            "academic_year": class_group.academic_year,
        }
        try:
            with transaction.atomic():
                class_group.enrollments.all().delete()
                class_group.students.all().update(class_group=None)
                ClassStudentAnalyticsPolicy.objects.filter(
                    class_group_id=class_group.pk
                ).delete()
                class_group.delete()
        except ProtectedError as exc:
            raise CourseClassDeletionError("班级仍被历史数据引用，不能删除。") from exc
        log_action(request, "class.delete", details=details)
    return details
