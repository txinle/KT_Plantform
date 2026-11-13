"""Whole-group structure and server-owned material projections."""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Prefetch

from ..models import Question, QuestionGroup
from .question_images import IMAGE_MARKER, question_image_content, validate_existing_image_references, with_question_images


BLANK_MARKER = re.compile(r'\{\{(b[0-9]{2,})\}\}')


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _members(group, members):
    if members is None and hasattr(group, '_read_members'):
        for member in group._read_members:
            member.group = group
            if member.bank_id == group.bank_id:
                member.bank = group.bank
        return group._read_members
    return list(members) if members is not None else list(group.members.order_by('group_order', 'pk').prefetch_related('options'))


def question_groups_for_read(queryset):
    from .question_kc_mapping import question_kc_mapping_prefetch
    members = Question.objects.order_by('group_order', 'pk').prefetch_related(
        'options', question_kc_mapping_prefetch(include_children=True))
    return queryset.select_related('bank__course').prefetch_related(
        Prefetch('members', queryset=members, to_attr='_read_members'))


def project_group_status(group):
    return {'kind': group.kind, 'status': 'draft' if group_readiness_issues(group) else 'active'}


def validate_group_structure(group, members=None):
    members = _members(group, members)
    errors = []
    kind, material = _get(group, 'kind'), _get(group, 'material', '')
    if kind not in QuestionGroup.Kind.values:
        errors.append('题组形式必须为 reading、words 或 cloze。')
    if not str(material).strip():
        errors.append('共享材料不能为空。')
    if not members:
        errors.append('题组至少包含一道成员题。')
    orders = [_get(member, 'group_order') for member in members]
    if any(isinstance(order, bool) or not isinstance(order, int) for order in orders) or sorted(orders) != list(range(1, len(members) + 1)):
        errors.append('组内顺序必须从 1 开始连续且不重复。')
    bank_id = _get(group, 'bank_id')
    if any(_get(member, 'bank_id', bank_id) != bank_id for member in members):
        errors.append('题组成员必须属于同一题库。')
    allowed_type = Question.Type.SINGLE if kind == QuestionGroup.Kind.READING else Question.Type.FILL_BLANK
    allowed_types = {Question.Type.FILL_BLANK, Question.Type.NUMERIC} if kind == 'fill_blank' else {allowed_type}
    if any(_get(member, 'question_type') not in allowed_types for member in members):
        errors.append('阅读题组仅支持单选题，选词和短文填空题组仅支持填空题。')
    blank_ids = [_get(member, 'blank_id', '') for member in members]
    markers = BLANK_MARKER.findall(material)
    if kind in (QuestionGroup.Kind.CLOZE, QuestionGroup.Kind.WORDS, QuestionGroup.Kind.FILL_BLANK):
        remaining = IMAGE_MARKER.sub('', BLANK_MARKER.sub('', material))
        malformed = '{{' in remaining or '}}' in remaining
        if malformed or any(not re.fullmatch(r'b[0-9]{2,}', blank or '') for blank in blank_ids) or len(set(blank_ids)) != len(blank_ids) or len(set(markers)) != len(markers) or sorted(markers) != sorted(blank_ids):
            errors.append('完形材料的 {{b01}} 空位必须与成员空位编号严格一一对应且不重复。')
    elif any(blank_ids) or markers:
        errors.append('阅读题组不能包含空位编号或完形空位标记。')
    words = _get(group, 'word_bank', [])
    if kind == QuestionGroup.Kind.WORDS:
        if not isinstance(words, list) or not words or any(not isinstance(word, str) or not word.strip() for word in words) or len(set(words)) != len(words):
            errors.append('选词填空需要非空且不重复的共享词库。')
        else:
            from .scoring import _normalized_text
            for member in members:
                answer = _get(member, 'answer', _get(member, 'correct_answer', ''))
                sensitive = _get(member, 'text_match_case_sensitive', False)
                normalize = lambda value: _normalized_text(value, case_sensitive=sensitive)
                if answer and normalize(answer) not in {normalize(word) for word in words}:
                    errors.append('选词填空的标准答案必须来自共享词库。')
    elif words:
        errors.append('只有选词填空可设置共享词库。')
    if errors:
        raise ValidationError(errors)
    return members


def material_segments(group, members=None):
    members = validate_group_structure(group, members)
    material = _get(group, 'material', '')
    if _get(group, 'kind') not in (QuestionGroup.Kind.CLOZE, QuestionGroup.Kind.WORDS, QuestionGroup.Kind.FILL_BLANK):
        return [{'kind': 'text', 'text': material}]
    lookup = {_get(member, 'blank_id'): str(_get(member, 'item_key') or _get(member, 'row_id') or _get(member, 'pk')) for member in members}
    segments, position = [], 0
    for marker in BLANK_MARKER.finditer(material):
        if marker.start() > position:
            segments.append({'kind': 'text', 'text': material[position:marker.start()]})
        blank = marker.group(1)
        segments.append({'kind': 'blank', 'blank_id': blank, 'item_key': lookup[blank]})
        position = marker.end()
    if position < len(material):
        segments.append({'kind': 'text', 'text': material[position:]})
    return segments


def group_readiness_issues(group, members=None):
    from .question_readiness import readiness_issues
    members = _members(group, members)
    try:
        validate_group_structure(group, members)
    except ValidationError as error:
        return error.messages
    return list(dict.fromkeys(issue for member in members for issue in readiness_issues(member, include_group=False)))


def project_question_group(group, *, teacher=True, include_images=True):
    from .question_kc_mapping import project_question_kc_mapping
    members = _members(group, None)
    issues = group_readiness_issues(group, members)
    projected = []
    for member in members:
        row = {
            'id': member.pk, 'item_key': str(member.pk), 'question_type': member.question_type,
            'text': member.text, 'options': [{'key': option.key, 'text': option.text} for option in member.options.all()],
            'group_order': member.group_order, 'blank_id': member.blank_id, 'max_score': format(member.max_score, '.2f'),
            'content_language': member.content_language,
        }
        if teacher:
            row.update(answer=member.correct_answer, accepted_answers=member.accepted_answers,
                absolute_tolerance=str(member.absolute_tolerance) if member.absolute_tolerance is not None else None,
                relative_tolerance=str(member.relative_tolerance) if member.relative_tolerance is not None else None,
                text_match_case_sensitive=member.text_match_case_sensitive, text_match_policy_version=member.text_match_policy_version,
                explanation=member.explanation, teacher_difficulty=member.teacher_difficulty, kc_mapping=project_question_kc_mapping(member), status=member.status)
            if group.bank.course_id is None:
                from .standalone_kc import project_standalone_primary
                standalone_mapping = project_standalone_primary(member)
                row['primary_concept_id'] = standalone_mapping['primary_concept_id'] if standalone_mapping else None
                row['kc_mapping'] = standalone_mapping or {'scope': 'standalone', 'primary_concept_id': None, 'nodes': []}
        projected.append(with_question_images(row) if include_images else row)
    try:
        segments = material_segments(group, members)
    except ValidationError:
        segments = []
    result = {
        'id': group.pk, 'kind': group.kind, 'title': group.title, 'instructions': group.instructions,
        'material': group.material, 'word_bank': group.word_bank, 'content_language': group.content_language, 'revision': group.revision,
        'material_segments': segments, 'members': projected, 'item_count': len(members),
        'total_max_score': format(sum((member.max_score for member in members), Decimal('0')), '.2f'),
        'status': 'draft' if issues else 'active', 'readiness_issues': issues,
    }
    if teacher and group.bank.course_id is None:
        from .standalone_kc import available_standalone_concepts
        result['available_concepts'] = [{'id': concept.pk, 'name': concept.name, 'code': concept.code} for concept in available_standalone_concepts(group.bank)]
    return with_question_images(result) if include_images else result


def project_bank_unit_summary(bank, groups):
    """Count each shared-material group once while retaining response-item counts."""
    from collections import Counter
    from django.db.models import Count
    counts = Counter()
    usable = 0
    ordinary = bank.questions.filter(group__isnull=True)
    for row in ordinary.values('question_type', 'educational_type', 'status').annotate(count=Count('pk')):
        kind = row['question_type'] if row['educational_type'] == 'standard' else row['educational_type']
        counts[kind] += row['count']
        if row['status'] == Question.Status.ACTIVE:
            usable += row['count']
    for group in groups:
        counts[group['kind']] += 1
        usable += group['status'] == 'active'
    labels = {**dict(Question.Type.choices), **dict(Question.EducationalType.choices), **dict(QuestionGroup.Kind.choices)}
    return {'complete_count': sum(counts.values()), 'response_item_count': bank.questions.count(), 'usable_count': usable,
        'types': [{'value': kind, 'label': labels[kind], 'count': count} for kind, count in counts.items()]}


class QuestionGroupRevisionConflict(Exception):
    pass


@transaction.atomic
def save_question_group(*, bank, data, group=None):
    from ..access import lock_writable_bank_content
    from ..models import QuestionOption
    from .lifecycle import delete_question
    from .question_kc_mapping import replace_question_kc_mapping, validate_question_kc_mapping
    from .question_readiness import refresh_question_readiness
    bank = lock_writable_bank_content(bank)
    existing = {}
    if group is not None:
        group = bank.groups.select_for_update().get(pk=group.pk)
        if data.get('expected_revision') != group.revision:
            raise QuestionGroupRevisionConflict('题组已被其他编辑修改，请刷新后重试。')
        existing = {member.pk: member for member in group.members.select_for_update().select_related('bank')}
    else:
        group = QuestionGroup(bank=bank)
    try:
        validate_existing_image_references([data.get('material', group.material)], [group.material])
    except ValueError as error:
        raise ValidationError(str(error)) from error
    for key in ('kind', 'title', 'instructions', 'material', 'content_language', 'word_bank'):
        setattr(group, key, data.get(key, getattr(group, key)))
    rows = data['members']
    if group.kind == 'fill_blank':
        from .multi_blank_import import blank_material
        # The editor sends ordinary underscores, not internal blank identities.
        if '{{b' not in group.material:
            group.material = blank_material(group.material, len(rows))
        rows = [{**row, 'group_order': index, 'blank_id': f'b{index:02}'} for index, row in enumerate(rows, 1)]
    ids = [row['id'] for row in rows if row.get('id') is not None]
    if len(ids) != len(set(ids)) or not set(ids) <= set(existing):
        raise ValidationError('成员题目必须属于当前题组且不能重复。')
    deleted_ids = set(existing) - set(ids)
    if deleted_ids and data.get('confirm_member_deletion') is not True:
        raise ValidationError('删除成员题目需要明确确认。')
    desired_members = []
    sequence = bank.questions.aggregate(value=Max('sequence'))['value'] or 0
    for row in rows:
        question = existing.get(row.get('id'))
        if question is None:
            sequence += 1
            question = Question(bank=bank, sequence=sequence, status=Question.Status.DRAFT)
        values = [row.get('text'), row.get('explanation'), *[option['text'] for option in row.get('options', [])]]
        try:
            validate_existing_image_references(values, question_image_content(question))
        except ValueError as error:
            raise ValidationError(str(error)) from error
        for field, default in (('question_type', ''), ('text', ''), ('accepted_answers', []), ('explanation', ''),
            ('max_score', Decimal('1')), ('text_match_case_sensitive', False), ('text_match_policy_version', 'text-exact-v1'),
            ('absolute_tolerance', None), ('relative_tolerance', None),
            ('group_order', None), ('blank_id', ''), ('content_language', group.content_language), ('teacher_difficulty', None)):
            setattr(question, field, row.get(field, default))
        question.correct_answer = row.get('answer', '')
        question.group = group
        mapping = row.get('kc_mapping')
        if bank.course_id is None:
            from .standalone_kc import standalone_primary_mapping
            if row.get('primary_concept_id') is not None:
                standalone_primary_mapping(bank, row['primary_concept_id'])
            if mapping and mapping.get('nodes') and mapping.get('scope') != 'standalone':
                raise ValidationError('独立题组请通过 primary_concept_id 选择已有私有或公共 KC。')
            desired_members.append(question)
            continue
        if mapping is not None:
            if not isinstance(mapping, dict) or not isinstance(mapping.get('nodes', []), list):
                raise ValidationError('KC 映射格式无效。')
            if mapping.get('nodes'):
                if bank.course_id is None:
                    raise ValidationError('独立题库不支持课程 KC 映射，请在课程题库中编辑。')
                validate_question_kc_mapping(question, root_kc_code=mapping.get('root_kc_code'), nodes=mapping['nodes'])
        desired_members.append(question)
    validate_group_structure(group, desired_members)
    group.revision = group.revision + 1 if group.pk else 1
    group.save()
    for question_id in deleted_ids:
        delete_question(existing[question_id], from_group=True)
    for question, row in zip(desired_members, rows):
        if question.status != Question.Status.ARCHIVED:
            question.status = Question.Status.DRAFT
        question.save()
        question.options.all().delete()
        QuestionOption.objects.bulk_create([
            QuestionOption(question=question, key=option['key'], text=option['text'], display_order=index)
            for index, option in enumerate(row.get('options', []), 1)
        ])
        mapping = row.get('kc_mapping')
        if bank.course_id is None:
            if 'primary_concept_id' in row:
                from .standalone_kc import replace_standalone_primary
                replace_standalone_primary(question, row['primary_concept_id'])
        elif mapping is not None:
            if mapping.get('nodes'):
                replace_question_kc_mapping(question, root_kc_code=mapping.get('root_kc_code'), nodes=mapping['nodes'])
            else:
                question.concept_links.all().delete()
    for question in desired_members:
        refresh_question_readiness(question)
    return group


@transaction.atomic
def delete_question_group(group):
    from ..access import lock_writable_bank_content
    from .lifecycle import delete_question
    bank = lock_writable_bank_content(group.bank)
    group = bank.groups.select_for_update().get(pk=group.pk)
    for member in group.members.select_for_update().order_by('pk'):
        delete_question(member, from_group=True)
    group.delete()
