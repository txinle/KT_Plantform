from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import ClassGroup


@contextmanager
def lock_active_class_groups(class_groups, *, inactive_error):
    class_ids = sorted(
        {
            class_id
            for class_group in class_groups
            if (class_id := getattr(class_group, "pk", class_group)) is not None
        }
    )
    with transaction.atomic():
        locked_classes = list(
            ClassGroup.objects.select_for_update()
            .filter(pk__in=class_ids)
            .order_by("pk")
        )
        locked_by_id = {class_group.pk: class_group for class_group in locked_classes}
        if len(locked_by_id) != len(class_ids):
            raise ValidationError("班级已变更，请刷新后重试。")

        inactive_class = next(
            (class_group for class_group in locked_classes if not class_group.is_active),
            None,
        )
        if inactive_class is not None:
            if callable(inactive_error):
                raise inactive_error(inactive_class)
            raise ValidationError(inactive_error)

        yield locked_by_id
