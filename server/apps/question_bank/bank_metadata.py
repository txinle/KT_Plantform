from apps.accounts.models import User
from .models import QuestionBank
from .forms import QuestionBankForm

ACTIVE_BANK_VISIBILITY_CHOICES = [
    (QuestionBank.Visibility.PRIVATE, QuestionBank.Visibility.PRIVATE.label),
    (QuestionBank.Visibility.SHARED, QuestionBank.Visibility.SHARED.label),
]


def can_edit_bank_metadata(user, course, bank):
    return bool(
        user.is_superuser
        or user.role == User.Role.ADMIN
        or course.can_manage(user)
        or bank.owner_id == user.id
    )

def _metadata_question_bank_form(data=None, *, instance):
    form = QuestionBankForm(data, instance=instance)
    if instance.visibility == QuestionBank.Visibility.ARCHIVED:
        form.fields["visibility"].choices = [
            (QuestionBank.Visibility.ARCHIVED, QuestionBank.Visibility.ARCHIVED.label),
            (QuestionBank.Visibility.PRIVATE, QuestionBank.Visibility.PRIVATE.label),
        ]
    else:
        form.fields["visibility"].choices = ACTIVE_BANK_VISIBILITY_CHOICES
    return form

def _shared_banks_for(course):
    return QuestionBank.objects.filter(
        visibility=QuestionBank.Visibility.SHARED,
        course__isnull=False,
    ).exclude(course=course)
