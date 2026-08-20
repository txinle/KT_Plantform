from django.core.exceptions import PermissionDenied

from .models import QuestionBank


ARCHIVED_BANK_CONTENT_WRITE_MESSAGE = "题库已归档，不能写入题目内容或 KC 映射。"


def require_writable_bank_content(bank):
    if bank.visibility == QuestionBank.Visibility.ARCHIVED:
        raise PermissionDenied(ARCHIVED_BANK_CONTENT_WRITE_MESSAGE)
    return bank
