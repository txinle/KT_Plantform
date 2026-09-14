from apps.accounts.research_consent import (
    CURRENT_RESEARCH_CONSENT_VERSION,
    accept_current_research_consent,
)


def accept_research_consent(*students):
    for student in students:
        accept_current_research_consent(
            student=student,
            version=CURRENT_RESEARCH_CONSENT_VERSION,
        )
