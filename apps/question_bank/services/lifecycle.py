from django.db.models.deletion import ProtectedError

from apps.experiments.models import QuestionSnapshot


class ReferencedQuestionBankError(Exception):
    pass


class ReferencedQuestionError(Exception):
    pass


def delete_question_bank(bank):
    question_count = bank.questions.count()
    if bank.experiments.exists() or QuestionSnapshot.objects.filter(
        source_question__bank=bank
    ).exists():
        raise ReferencedQuestionBankError
    details = {
        "bank_id": bank.pk,
        "course_id": bank.course_id,
        "name": bank.name,
        "question_count": question_count,
    }
    try:
        bank.delete()
    except ProtectedError as exc:
        raise ReferencedQuestionBankError from exc
    return details


def delete_question(question):
    if question.experiments.exists() or QuestionSnapshot.objects.filter(
        source_question=question
    ).exists():
        raise ReferencedQuestionError
    identity = getattr(question, "course_identity", None)
    details = {
        "question_id": question.pk,
        "bank_id": question.bank_id,
        "course_id": question.bank.course_id,
        "sequence": question.sequence,
        "code": question.code,
        "stable_code": identity.stable_code if identity else "",
    }
    try:
        question.delete()
    except ProtectedError as exc:
        raise ReferencedQuestionError from exc
    return details
