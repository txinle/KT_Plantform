import os
import re

from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured


MODULE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _parse_id_allowlist(setting_name):
    raw_value = os.getenv(setting_name, "").strip()
    if not raw_value:
        return frozenset()
    try:
        values = frozenset(int(value.strip()) for value in raw_value.split(","))
    except ValueError as error:
        raise ImproperlyConfigured(
            f"{setting_name} must contain comma-separated positive IDs."
        ) from error
    if any(value <= 0 for value in values):
        raise ImproperlyConfigured(
            f"{setting_name} must contain comma-separated positive IDs."
        )
    return values


@register()
def check_vue_rollout_settings(app_configs, **kwargs):
    errors = []
    for setting_name, value in sorted(os.environ.items()):
        if not setting_name.startswith("VUE_"):
            continue
        if setting_name.endswith(("_COURSE_IDS", "_USER_IDS")):
            try:
                _parse_id_allowlist(setting_name)
            except ImproperlyConfigured as error:
                errors.append(Error(str(error), id="api.E001"))
        elif setting_name.endswith("_ENABLED") and value not in {"", "0", "1"}:
            errors.append(
                Error(
                    f'{setting_name} must be "0" or "1".',
                    id="api.E002",
                )
            )
    return errors


def _task_course_id(task):
    if task is None:
        return None
    class_group = getattr(task, "class_group", None)
    if class_group is not None and getattr(class_group, "course_id", None):
        return class_group.course_id
    experiment = getattr(task, "experiment", None)
    if experiment is not None and getattr(experiment, "course_id", None):
        return experiment.course_id
    class_task = getattr(task, "class_task", None)
    if class_task is not None and class_task is not task:
        return _task_course_id(class_task)
    return None


def is_vue_module_enabled(module, user, course=None, task=None):
    if not MODULE_NAME_PATTERN.fullmatch(module):
        raise ImproperlyConfigured("Vue module names must use lowercase snake_case.")
    if not getattr(user, "is_authenticated", False):
        return False

    prefix = f"VUE_{module.upper()}"
    if os.getenv(f"{prefix}_ENABLED", "0") != "1":
        return False

    course_ids = _parse_id_allowlist(f"{prefix}_COURSE_IDS")
    user_ids = _parse_id_allowlist(f"{prefix}_USER_IDS")
    if not course_ids and not user_ids:
        return True

    user_id = getattr(user, "pk", None)
    if user_id in user_ids:
        return True
    course_id = getattr(course, "pk", None) if course is not None else _task_course_id(task)
    return course_id in course_ids
