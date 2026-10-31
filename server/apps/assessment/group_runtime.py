"""Student-owned group projections built exclusively from published snapshots."""
from django.urls import reverse

from .models import StudentQuestionAssignment


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
