from collections.abc import Mapping

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router, transaction

from apps.audit.models import AuditLog

from .models import ClassGroup, ClassStudentAnalyticsPolicy, Course


POLICY_FIELD_NAMES = (
    "show_scores",
    "show_correct_rate",
    "show_item_results",
    "show_class_average",
    "rank_mode",
    "show_kc_rates",
    "show_trend",
)
_BOOLEAN_POLICY_FIELD_NAMES = frozenset(POLICY_FIELD_NAMES) - {"rank_mode"}
_RANK_MODE_VALUES = frozenset(ClassStudentAnalyticsPolicy.RankMode.values)


def _validate_changes(changes):
    if not isinstance(changes, Mapping):
        raise ValidationError({"changes": "策略变更必须是字段到值的映射。"})

    unknown_fields = set(changes).difference(POLICY_FIELD_NAMES)
    if unknown_fields:
        raise ValidationError(
            {field: "不是可更新的学生分析策略字段。" for field in unknown_fields}
        )

    validated_changes = {}
    for field, value in changes.items():
        if field in _BOOLEAN_POLICY_FIELD_NAMES:
            if type(value) is not bool:
                raise ValidationError({field: "必须为布尔值。"})
            validated_changes[field] = value
            continue
        if not isinstance(value, str) or value not in _RANK_MODE_VALUES:
            raise ValidationError({field: "不是受支持的排名展示方式。"})
        validated_changes[field] = str(value)
    return validated_changes


def _policy_values(policy):
    values = {field: getattr(policy, field) for field in POLICY_FIELD_NAMES}
    values["rank_mode"] = str(values["rank_mode"])
    return values


def _normalize_policy_values(values):
    if not values["show_correct_rate"]:
        values["show_kc_rates"] = False
        values["show_trend"] = False
    if not values["show_scores"]:
        values["rank_mode"] = ClassStudentAnalyticsPolicy.RankMode.NONE
    return values


def update_class_student_analytics_policy(*, class_group, actor, changes):
    """Persist a class policy change and its minimal audit event atomically."""
    validated_changes = _validate_changes(changes)
    class_group_id = getattr(class_group, "pk", None)
    if class_group_id is None:
        raise ValidationError({"class_group": "必须提供已保存的班级。"})

    using = router.db_for_write(ClassGroup, instance=class_group)
    with transaction.atomic(using=using):
        try:
            initial_course_id = (
                ClassGroup._base_manager.using(using)
                .values_list("course_id", flat=True)
                .get(pk=class_group_id)
            )
        except ClassGroup.DoesNotExist as exc:
            raise ValidationError({"class_group": "班级已不存在。"}) from exc
        if initial_course_id is None:
            raise ValidationError({"class_group": "遗留班级尚未关联课程。"})

        locked_course = (
            Course._base_manager.using(using)
            .select_for_update()
            .get(pk=initial_course_id)
        )
        try:
            locked_class_group = (
                ClassGroup._base_manager.using(using)
                .select_for_update()
                .get(pk=class_group_id)
            )
        except ClassGroup.DoesNotExist as exc:
            raise ValidationError({"class_group": "班级已不存在。"}) from exc
        if locked_class_group.course_id is None:
            raise ValidationError({"class_group": "遗留班级尚未关联课程。"})
        try:
            policy = (
                ClassStudentAnalyticsPolicy._base_manager.using(using)
                .select_for_update()
                .get(class_group_id=locked_class_group.pk)
            )
        except ClassStudentAnalyticsPolicy.DoesNotExist as exc:
            raise ValidationError({"class_group": "班级学生分析策略尚未初始化。"}) from exc

        if locked_class_group.course_id != locked_course.pk:
            raise ValidationError({"class_group": "班级所属课程已变更，请刷新后重试。"})
        if locked_course.status != Course.Status.ACTIVE:
            raise PermissionDenied("课程已归档，不能更新学生分析策略。")
        if not locked_course.can_manage(actor, using=using):
            raise PermissionDenied("没有管理该课程学生分析策略的权限。")

        before = _policy_values(policy)
        after = _normalize_policy_values({**before, **validated_changes})
        changed_fields = [
            field for field in POLICY_FIELD_NAMES if before[field] != after[field]
        ]
        if not changed_fields:
            return policy

        for field in changed_fields:
            setattr(policy, field, after[field])
        policy.updated_by = actor
        policy.save(
            using=using,
            update_fields=[*changed_fields, "updated_by", "updated_at"],
        )
        after = _policy_values(policy)
        AuditLog.objects.using(using).create(
            actor=actor,
            action="class.student_analytics_policy.update",
            object_type="ClassStudentAnalyticsPolicy",
            object_id=str(policy.pk),
            details={
                "course_id": locked_course.pk,
                "class_group_id": locked_class_group.pk,
                "policy_id": policy.pk,
                "before": before,
                "after": after,
                "changed_fields": changed_fields,
            },
        )
        return policy
