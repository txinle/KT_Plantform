"""Student-owned group projections built exclusively from published snapshots."""
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from .models import StudentQuestionAssignment


def presentation_runtime_projection(session, active_attempt):
    """Project complete-question status without entering any member attempt."""
    from .api.views import _runtime_pending_confidence
    from .deadline_services import DeadlineDecisionService
    from .models import Attempt, ConfidenceAssignment, ExperimentSession
    assignments = list(StudentQuestionAssignment.objects.filter(session=session).select_related(
        'snapshot__group_snapshot', 'attempt').order_by('assigned_sequence_index'))
    confidence = {row.attempt_id: row for row in ConfidenceAssignment.objects.filter(attempt__session=session)}
    units, grouped, states = [], {}, {}
    active_group = active_attempt.assignment.snapshot.group_snapshot_id
    now = timezone.now()
    for assignment in assignments:
        snapshot, attempt = assignment.snapshot, assignment.attempt
        attempt.session = session
        attempt.assignment = assignment
        identity = str(snapshot.group_snapshot_id) if snapshot.group_snapshot_id else None
        group = snapshot.group_snapshot if identity else None
        unit = grouped.get(identity) if identity else None
        if unit is None:
            unit = {'group_id': identity, 'kind': group.kind if group else (snapshot.educational_type if snapshot.educational_type != 'standard' else snapshot.question_type),
                    'title': group.title if group else snapshot.title or snapshot.code, 'assignment_ids': [],
                    'member_count': 0, 'answered_count': 0, 'maximum_score': Decimal('0')}
            units.append(unit)
            if identity:
                grouped[identity] = unit
        unit['assignment_ids'].append(assignment.pk)
        unit['member_count'] += 1
        unit['maximum_score'] += snapshot.max_score
        answer = attempt.draft_answer
        answered = answer is not None and answer != '' and answer != []
        if isinstance(answer, str):
            answered = bool(answer.strip())
        unit['answered_count'] += int(answered)
        if active_group and snapshot.group_snapshot_id == active_group:
            assigned = confidence.get(attempt.pk)
            if assigned:
                assigned.attempt = attempt
            writable = (session.status == ExperimentSession.Status.IN_PROGRESS
                and attempt.status not in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED)
                and DeadlineDecisionService.can_write_response(session.class_task, session, attempt, now).allowed)
            states[str(assignment.pk)] = {'status': attempt.status, 'response_write_allowed': writable,
                'has_response': answered,
                'pending_confidence': _runtime_pending_confidence(attempt, assigned)
                    if assigned and assigned.condition == ConfidenceAssignment.Condition.PROMPT else None,
                'inline_confidence': {'probability': assigned.draft_probability,
                    'answer_event_id': str(assigned.draft_answer_event_id) if assigned.draft_answer_event_id else None} if assigned else None}
    maximum = sum((unit['maximum_score'] for unit in units), Decimal('0'))
    for unit in units:
        unit['maximum_score'] = format(unit['maximum_score'], '.2f')
        unit['status'] = 'answered' if unit['answered_count'] == unit['member_count'] else 'partial' if unit['answered_count'] else 'unanswered'
    return {'presentation_units': units, 'group_member_state': states,
        'presentation_progress': {'total': len(units), 'answered': sum(unit['status'] == 'answered' for unit in units),
            'maximum_score': format(maximum, '.2f')}}


def group_runtime_projection(attempt):
    from apps.question_bank.services.question_groups import material_segments
    from .api.views import _runtime_options

    snapshot = attempt.assignment.snapshot
    if not snapshot.group_snapshot_id:
        return {}
    group = snapshot.group_snapshot
    assignments = list(StudentQuestionAssignment.objects.filter(session=attempt.session,
        snapshot__group_snapshot=group).select_related('snapshot', 'item_instance', 'attempt').order_by('snapshot__group_order'))
    members, responses = [], {}
    for assignment in assignments:
        item = assignment.snapshot
        key = str(assignment.pk)
        members.append({'item_key': key, 'assignment_id': assignment.pk, 'attempt_id': assignment.attempt.pk,
            'question_type': item.question_type, 'text': item.text, 'options': _runtime_options(assignment),
            'group_order': item.group_order, 'blank_id': item.blank_id, 'max_score': format(item.max_score, '.2f'),
            'status': assignment.attempt.status,
            'url': reverse('assessment:question', args=[attempt.session_id, assignment.pk])})
        responses[key] = assignment.attempt.draft_answer
    return {'question_group': {'group_id': str(group.pk), 'source_group_id': group.source_group_id,
        'kind': group.kind, 'title': group.title, 'instructions': group.instructions, 'material': group.material,
        'content_language': group.content_language, 'word_bank': list(group.word_bank),
        'material_segments': material_segments(group, members), 'members': members},
        'group_responses': responses}


def prepare_group_observation(parsed):
    from .event_batch_services import ProcessRequestError
    if not parsed.session.version.configuration.get('question_group_runtime_version'):
        raise ProcessRequestError('group_contract_unavailable')
    group_id = parsed.payload['group_snapshot_id']
    if not parsed.session.assignments.filter(snapshot__group_snapshot_id=group_id).exists():
        raise ProcessRequestError('group_not_assigned')
    if parsed.event_type == 'group_presented' and parsed.attempt.assignment.snapshot.group_snapshot_id != group_id:
        raise ProcessRequestError('group_presentation_target_mismatch')
    next_group = parsed.payload.get('next_group_snapshot_id')
    if next_group is not None and not parsed.session.assignments.filter(snapshot__group_snapshot_id=next_group).exists():
        raise ProcessRequestError('group_not_assigned')
    return parsed.payload
