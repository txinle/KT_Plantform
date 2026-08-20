from time import sleep

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Max

from apps.accounts.course_access import lock_course

from ..models import CourseQuestionIdentity, Question


MAX_ALLOCATION_RETRIES = 5


def stable_course_question_code(course_id, course_sequence):
    return f"C{course_id:06d}-Q{course_sequence:06d}"


def allocate_course_question_identity(course_or_pk, question_or_pk):
    """Assign a never-reused course sequence to a question in that course."""
    course_id = getattr(course_or_pk, "pk", course_or_pk)
    question_id = getattr(question_or_pk, "pk", question_or_pk)
    last_error = None

    for attempt in range(MAX_ALLOCATION_RETRIES):
        try:
            with transaction.atomic():
                course = lock_course(course_id)
                question = Question.objects.select_related("bank").get(pk=question_id)
                if question.bank.course_id != course.pk:
                    raise ValueError("题目必须属于指定课程。")

                existing = CourseQuestionIdentity.objects.filter(question_id=question.pk).first()
                if existing is not None:
                    if existing.course_id != course.pk:
                        raise ValueError("题目已关联到其他课程的永久题目标识。")
                    return existing

                maximum = CourseQuestionIdentity.objects.filter(course=course).aggregate(
                    maximum=Max("course_sequence")
                )["maximum"]
                course_sequence = (maximum or 0) + 1
                return CourseQuestionIdentity.objects.create(
                    course=course,
                    question=question,
                    course_sequence=course_sequence,
                    stable_code=stable_course_question_code(course.pk, course_sequence),
                )
        except IntegrityError as exc:
            last_error = exc
            existing = CourseQuestionIdentity.objects.filter(question_id=question_id).first()
            if existing is not None:
                return existing
        except OperationalError as exc:
            if "locked" not in str(exc).casefold():
                raise
            last_error = exc

        if attempt < MAX_ALLOCATION_RETRIES - 1:
            sleep(0.01 * (attempt + 1))

    raise last_error
