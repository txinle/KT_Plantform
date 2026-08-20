from django.core.exceptions import PermissionDenied

from .models import QuestionBank


ARCHIVED_BANK_CONTENT_WRITE_MESSAGE = "题库已归档，不能写入题目内容或 KC 映射。"


def require_writable_bank_content(bank):
    if bank.visibility == QuestionBank.Visibility.ARCHIVED:
        raise PermissionDenied(ARCHIVED_BANK_CONTENT_WRITE_MESSAGE)
    return bank


def lock_question_bank(bank_or_pk):
    bank_pk = getattr(bank_or_pk, "pk", bank_or_pk)
    return QuestionBank.objects.select_for_update().get(pk=bank_pk)


def lock_writable_bank_content(bank_or_pk):
    return require_writable_bank_content(lock_question_bank(bank_or_pk))
