"""Compose confirmed source mutations with a separately committed target entry."""
from django.conf import settings
from django.db import DatabaseError
from django.http import Http404
from rest_framework.exceptions import APIException, ValidationError as ApiValidationError

from .event_batch_services import ProcessRequestError, require_exact_mapping
from .models import Attempt

ENTRY_FIELDS = frozenset({'assignment_id', 'reason', 'navigation_id', 'client_occurred_at',
                          'client_monotonic_ms', 'client_time_origin'})


def transition_protocol_for_start(student, experiment, capture_contract):
    allowed = ('*' in settings.ASSESSMENT_TRANSITION_USER_IDS
               or '*' in settings.ASSESSMENT_TRANSITION_COURSE_IDS
               or str(student.pk) in settings.ASSESSMENT_TRANSITION_USER_IDS
               or str(experiment.course_id) in settings.ASSESSMENT_TRANSITION_COURSE_IDS)
    return int(bool(settings.ASSESSMENT_TRANSITION_ENABLED and allowed
                    and capture_contract.contract_name == 'capture-v2'))


def validate_next_entry(attempt, document, *, destination_id=None):
    if 'next_entry' not in document:
        return
    if attempt.session.transition_protocol_version != 1:
        raise ProcessRequestError('transition_entry_disabled', status=409)
    entry = document['next_entry']
    require_exact_mapping(entry, allowed=ENTRY_FIELDS, required=ENTRY_FIELDS)
    assignment_id = entry['assignment_id']
    if (type(assignment_id) is not int or assignment_id <= 0 or assignment_id == attempt.assignment_id
            or destination_id is not None and assignment_id != destination_id):
        raise ProcessRequestError('invalid_navigation_destination')
    from .api.serializers import StudentRuntimeQuestionEnterSerializer
    serializer = StudentRuntimeQuestionEnterSerializer(data={key: value for key, value in entry.items()
                                                            if key != 'assignment_id'})
    try:
        serializer.is_valid(raise_exception=True)
    except ApiValidationError as error:
        raise ProcessRequestError('invalid_next_entry') from error


def append_next_entry(request, attempt_id, document, receipt):
    """Called only after the source service's durable transaction has returned."""
    if 'next_entry' not in document:
        return receipt
    from .api.views import StudentRuntimeQuestionEntryView
    entry = document['next_entry']
    try:
        session_id = Attempt.objects.values_list('session_id', flat=True).get(pk=attempt_id, session__student=request.user)
        response = StudentRuntimeQuestionEntryView().enter(request, session_id, entry['assignment_id'],
            {key: value for key, value in entry.items() if key != 'assignment_id'})
        outcome = {'committed': True, 'runtime': response.data}
    except (APIException, Http404) as error:
        outcome = {'committed': False, 'error': {
            'code': getattr(error, 'default_code', 'NOT_FOUND'),
            'message': str(getattr(error, 'detail', error)),
        }}
    except DatabaseError:
        # COMMIT may have succeeded. Replaying the frozen entry ID resolves that uncertainty.
        outcome = {'committed': False, 'error': {
            'code': 'ENTRY_OUTCOME_UNKNOWN', 'message': '进入结果尚未确认，请重试原操作。',
        }}
    return {**receipt, 'next_entry': outcome}
