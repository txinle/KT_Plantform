from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import transaction

from .course_access import lock_writable_course
from .models import ClassGroup


@contextmanager
def lock_active_class_groups(class_groups, *, inactive_error, lock_courses=False):
    class_ids = sorted(
        {
            class_id
            for class_group in class_groups
            if (class_id := getattr(class_group, "pk", class_group)) is not None
        }
    )
    with transaction.atomic():
        initial_course_ids = None
        if lock_courses:
            initial_course_ids = dict(
                ClassGroup.objects.filter(pk__in=class_ids).values_list("pk", "course_id")
            )
            if len(initial_course_ids) != len(class_ids):
                raise ValidationError("班级已变更，请刷新后重试。")
            for course_id in sorted(
                {course_id for course_id in initial_course_ids.values() if course_id is not None}
            ):
                lock_writable_course(course_id)

        locked_classes = list(
            ClassGroup.objects.select_for_update()
            .filter(pk__in=class_ids)
            .order_by("pk")
        )
        locked_by_id = {class_group.pk: class_group for class_group in locked_classes}
        if len(locked_by_id) != len(class_ids):
            raise ValidationError("班级已变更，请刷新后重试。")
        if lock_courses and any(
            locked_by_id[class_id].course_id != initial_course_ids[class_id]
            for class_id in class_ids
        ):
            raise ValidationError("班级所属课程已变更，请刷新后重试。")

        inactive_class = next(
            (class_group for class_group in locked_classes if not class_group.is_active),
            None,
        )
        if inactive_class is not None:
            if callable(inactive_error):
                raise inactive_error(inactive_class)
            raise ValidationError(inactive_error)

        yield locked_by_id
