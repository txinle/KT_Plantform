"""Authoritative course-experiment question-bank selection."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.question_bank.models import QuestionBank
from .models import Experiment, ExperimentQuestionBank, PublishPlan


def validate_question_banks(course, bank_ids, *, lock=False):
    ids = list(bank_ids)
    if not ids:
        raise ValidationError({"question_bank_ids": "至少选择一个题库，请重新选择题库。"})
    if len(ids) != len(set(ids)):
        raise ValidationError({"question_bank_ids": "题库不能重复选择。"})
    queryset = QuestionBank.objects.filter(pk__in=ids, course=course)
    if lock:
        queryset = queryset.select_for_update()
    banks = list(queryset)
    if len(banks) != len(ids) or any(bank.visibility == QuestionBank.Visibility.ARCHIVED for bank in banks):
        raise ValidationError({"question_bank_ids": "题库必须属于当前课程且不能是归档题库。"})
    banks_by_id = {bank.pk: bank for bank in banks}
    return [banks_by_id[bank_id] for bank_id in ids]


@transaction.atomic
def replace_question_banks(experiment, bank_ids):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.status != Experiment.Status.DRAFT or experiment.frozen_release_snapshot_id or experiment.class_tasks.exists():
        raise ValidationError("实验定义已冻结，不能再修改题库。")
    banks = validate_question_banks(experiment.course, bank_ids, lock=True)
    previous_ids = list(
        experiment.question_bank_links.order_by("selection_order", "question_bank_id")
        .values_list("question_bank_id", flat=True)
    )
    experiment.question_bank_links.all().delete()
    ExperimentQuestionBank.objects.bulk_create([
        ExperimentQuestionBank(experiment=experiment, question_bank=bank, selection_order=index)
        for index, bank in enumerate(banks)
    ])
    # Legacy readers receive only a projection; candidate selection uses the relation.
    experiment.question_bank = banks[0]
    experiment.save(update_fields=["question_bank", "updated_at"])
    # A preview is tied to the frozen definition fingerprint. Changing the
    # selected bank set must require a new preview before commit.
    if sorted(previous_ids) != sorted(bank_ids):
        from .publication_services import _invalidate_current_preview

        plans = PublishPlan.objects.select_for_update().filter(
            experiment=experiment,
            status=PublishPlan.Status.PREVIEWED,
            preview_run__isnull=False,
        )
        for plan in plans:
            _invalidate_current_preview(plan, reason="question_banks_changed")
            plan.plan_revision += 1
            plan.save(update_fields=["plan_revision"])
    return banks
