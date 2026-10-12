from django.core.exceptions import PermissionDenied

from apps.accounts.course_access import lock_writable_course

from .models import QuestionBank


ARCHIVED_BANK_CONTENT_WRITE_MESSAGE = "题库已归档，不能写入题目内容或 KC 映射。"


def require_writable_bank_content(bank):
    if bank.visibility == QuestionBank.Visibility.ARCHIVED:
        raise PermissionDenied(ARCHIVED_BANK_CONTENT_WRITE_MESSAGE)
    return bank


def lock_question_bank(bank_or_pk):
    bank_pk = getattr(bank_or_pk, "pk", bank_or_pk)
    return QuestionBank.objects.select_for_update().get(pk=bank_pk)


def lock_writable_course_bank(course_or_pk, bank_or_pk, *, content=False):
    course = lock_writable_course(course_or_pk)
    bank = lock_question_bank(bank_or_pk)
    if bank.course_id != course.pk:
        raise PermissionDenied("题库不属于当前课程。")
    if content:
        require_writable_bank_content(bank)
    return course, bank


def lock_writable_bank_content(bank_or_pk):
    bank_pk = getattr(bank_or_pk, "pk", bank_or_pk)
    course_id = QuestionBank.objects.values_list("course_id", flat=True).get(pk=bank_pk)
    if course_id:
        lock_writable_course(course_id)
    return require_writable_bank_content(lock_question_bank(bank_pk))
