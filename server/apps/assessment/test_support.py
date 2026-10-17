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


class HistoricalSubmissionResult:
    """Compatibility assertion shape for historical domain tests, never an HTTP adapter."""
    request = {'PATH_INFO': 'historical-domain-service'}

    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.content = repr(data).encode()

    def json(self):
        return self.data

    def has_header(self, _name):
        return False


def historical_submission_result(*, student, attempt_id, document):
    """Exercise retained grading/evidence services without reopening the retired route."""
    import uuid
    from django.core.exceptions import ValidationError
    from django.utils.dateparse import parse_datetime
    from .atomic_submission_services import submit_attempt_bundle
    from .event_batch_services import ProcessRequestError
    from .feedback_services import frozen_feedback_payload
    from .services import submit_attempt
    from .writer_lease_services import WriterLeaseConflict

    try:
        if 'atomic_submission' in document:
            attempt, receipt = submit_attempt_bundle(student=student, attempt_id=attempt_id, document=document)
        else:
            attempt = submit_attempt(
                attempt_id, uuid.UUID(str(document['submission_token'])),
                parse_datetime(document['client_clicked_at']) if document.get('client_clicked_at') else None,
                target_event_id=document.get('target_event_id'),
                writer_lease_token=document.get('writer_lease_token'),
                writer_client_instance_id=document.get('writer_client_instance_id'),
                confidence_response=document.get('confidence'),
            )
            receipt = {'status': attempt.status, 'confidence_required': False, **frozen_feedback_payload(attempt)}
        return HistoricalSubmissionResult(receipt)
    except ProcessRequestError as error:
        return HistoricalSubmissionResult({'error': {'code': error.code}}, error.status)
    except WriterLeaseConflict:
        return HistoricalSubmissionResult({'error': {'code': 'writer_lease_conflict'}}, 409)
    except ValidationError as error:
        return HistoricalSubmissionResult({'error': {'code': 'VALIDATION_ERROR', 'messages': error.messages}}, 400)
