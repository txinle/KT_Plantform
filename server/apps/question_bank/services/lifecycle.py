from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from apps.accounts.course_access import lock_course
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentQuestionBank

from .course_question_identity import (
    CourseQuestionRenumberingBlocked,
    renumber_course_question_identities,
)


class ReferencedQuestionBankError(Exception):
    pass


class ReferencedQuestionError(Exception):
    pass


def _experiment_names(queryset):
    return list(queryset.order_by("name", "pk").values_list("name", flat=True).distinct())


def _reference_message(subject, experiment_names):
    names = "、".join(f"“{name}”" for name in experiment_names)
    return f"{subject}会影响以下正式实验：{names}。请保留并归档，或先处理这些实验。"


@transaction.atomic
def delete_question_bank(bank):
    course_id = type(bank).objects.values_list("course_id", flat=True).get(pk=bank.pk)
    if course_id is not None:
        lock_course(course_id)
    bank = type(bank).objects.select_for_update().get(pk=bank.pk)
    question_count = bank.questions.count()
    questions = list(bank.questions.select_for_update().order_by("pk"))
    question_ids = [question.pk for question in questions]
    blocking_experiments = _experiment_names(
        Experiment.objects.filter(Q(question_bank=bank) | Q(question_bank_links__question_bank=bank)).exclude(status=Experiment.Status.DRAFT)
    )
    blocking_link_experiments = _experiment_names(
        Experiment.objects.filter(question_links__question_id__in=question_ids)
        .exclude(status=Experiment.Status.DRAFT)
    )
    blocking_names = list(dict.fromkeys([*blocking_experiments, *blocking_link_experiments]))
    if blocking_names:
        raise ReferencedQuestionBankError(_reference_message("删除题库", blocking_names))
    try:
        numbering = renumber_course_question_identities(
            bank.course_id,
            remove_question_ids=question_ids,
            reason="course_question_bank_deleted",
        )
    except CourseQuestionRenumberingBlocked as exc:
        raise ReferencedQuestionBankError(str(exc)) from exc

    draft_experiments = Experiment.objects.select_for_update().filter(
        question_bank=bank,
        status=Experiment.Status.DRAFT,
    )
    ExperimentQuestion.objects.filter(
        question_id__in=question_ids,
        experiment__status=Experiment.Status.DRAFT,
    ).delete()
    affected_ids = list(ExperimentQuestionBank.objects.filter(question_bank=bank).values_list("experiment_id", flat=True))
    ExperimentQuestionBank.objects.filter(question_bank=bank).delete()
    draft_experiments.update(question_bank=None)
    for experiment in Experiment.objects.filter(pk__in=affected_ids, question_bank__isnull=True):
        remaining = experiment.question_bank_links.order_by("selection_order").first()
        if remaining is not None:
            experiment.question_bank_id = remaining.question_bank_id
            experiment.save(update_fields=["question_bank"])

    details = {
        "bank_id": bank.pk,
        "course_id": bank.course_id,
        "name": bank.name,
        "question_count": question_count,
        "renumbered_question_count": len(numbering["mappings"]),
        "retired_export_count": numbering["export_count"],
        "cleanup_pending": numbering["cleanup_pending"],
        "cleanup_id": numbering["cleanup_id"],
    }
    try:
        bank.delete()
    except ProtectedError as exc:
        raise ReferencedQuestionBankError("题库仍被正式实验或发布快照引用，不能永久删除。") from exc
    return details


@transaction.atomic
def delete_question(question):
    course_id = type(question).objects.values_list(
        "bank__course_id", flat=True
    ).get(pk=question.pk)
    if course_id is not None:
        lock_course(course_id)
    question = type(question).objects.select_for_update().select_related("bank").get(pk=question.pk)
    blocking_experiments = _experiment_names(
        Experiment.objects.filter(question_links__question=question)
        .exclude(status=Experiment.Status.DRAFT)
    )
    if blocking_experiments:
        raise ReferencedQuestionError(_reference_message("删除题目", blocking_experiments))
    identity = getattr(question, "course_identity", None)
    details = {
        "question_id": question.pk,
        "bank_id": question.bank_id,
        "course_id": question.bank.course_id,
        "sequence": question.sequence,
        "code": question.code,
        "stable_code": identity.stable_code if identity else "",
    }
    if question.bank.course_id is not None:
        try:
            numbering = renumber_course_question_identities(
                question.bank.course_id,
                remove_question_ids=[question.pk],
                reason="course_question_deleted",
            )
        except CourseQuestionRenumberingBlocked as exc:
            raise ReferencedQuestionError(str(exc)) from exc
        details.update(
            renumbered_question_count=len(numbering["mappings"]),
            retired_export_count=numbering["export_count"],
            cleanup_pending=numbering["cleanup_pending"],
            cleanup_id=numbering["cleanup_id"],
        )
    else:
        details.update(
            renumbered_question_count=0,
            retired_export_count=0,
            cleanup_pending=False,
            cleanup_id=None,
        )
    ExperimentQuestion.objects.filter(
        question=question,
        experiment__status=Experiment.Status.DRAFT,
    ).delete()
    try:
        question.delete()
    except ProtectedError as exc:
        raise ReferencedQuestionError("题目仍被正式实验或发布快照引用，不能永久删除。") from exc
    return details
