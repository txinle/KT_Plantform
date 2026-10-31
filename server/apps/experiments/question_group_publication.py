"""Frozen scoring fields and course question-group publication facts."""
from django.core.exceptions import ValidationError

from .models import QuestionGroupSnapshot


def validate_group_publication(experiment, questions):
    from apps.question_bank.services.question_groups import validate_group_structure

    questions = list(questions)
    groups = {question.group_id: question.group for question in questions if question.group_id}
    if not groups:
        return
    if experiment.course_id is None:
        raise ValidationError('题组正式测评仅支持课程实验，请在课程实验中发布题组。')
    if experiment.time_mode == 'per_question':
        raise ValidationError('题组首版不支持逐小题限时。')
    selected = {question.pk for question in questions}
    for group in groups.values():
        validate_group_structure(group)
        if set(group.members.values_list('pk', flat=True)) - selected:
            raise ValidationError(f'题组“{group.title or group.pk}”成员未完整选入，请补全成员后发布。')


def frozen_question_fields(version, question):
    fields = {
        'max_score': question.max_score,
        'content_language': question.group.content_language if question.group_id else question.content_language,
        'text_match_case_sensitive': question.text_match_case_sensitive,
        'text_match_policy_version': question.text_match_policy_version,
        'group_order': question.group_order,
        'blank_id': question.blank_id,
        'educational_type': question.educational_type,
        'title': question.title,
        'instructions': question.instructions,
    }
    if question.group_id:
        group = question.group
        frozen, _ = QuestionGroupSnapshot.objects.get_or_create(version=version, source_group_id=group.pk, defaults={
            'source_revision': group.revision, 'kind': group.kind, 'title': group.title,
            'instructions': group.instructions, 'material': group.material, 'content_language': group.content_language,
            'word_bank': list(group.word_bank),
        })
        fields['group_snapshot'] = frozen
    return fields


def group_units(items):
    units, grouped = [], {}
    for item in items:
        if item.group_snapshot_id:
            if item.group_snapshot_id not in grouped:
                grouped[item.group_snapshot_id] = []
                units.append(grouped[item.group_snapshot_id])
            grouped[item.group_snapshot_id].append(item)
        else:
            units.append([item])
    for unit in grouped.values():
        unit.sort(key=lambda item: item.group_order)
    return units


def release_extra_facts(question):
    facts = {}
    if question.title or question.instructions:
        facts['display_content'] = {'title': question.title, 'instructions': question.instructions}
    if question.educational_type != 'standard':
        facts['educational_type'] = question.educational_type
    if question.max_score != 1 or question.text_match_case_sensitive or question.content_language != 'und' or question.text_match_policy_version != 'text-exact-v1':
        facts['scoring_v1'] = {'max_score': question.max_score, 'case_sensitive': question.text_match_case_sensitive,
            'policy_version': question.text_match_policy_version, 'content_language': question.content_language}
    if question.group_id:
        group = question.group
        facts['group_v1'] = {'id': group.pk, 'revision': group.revision, 'kind': group.kind, 'title': group.title,
            'instructions': group.instructions, 'material': group.material, 'content_language': group.content_language,
            'group_order': question.group_order, 'blank_id': question.blank_id, 'word_bank': list(group.word_bank)}
    return facts
