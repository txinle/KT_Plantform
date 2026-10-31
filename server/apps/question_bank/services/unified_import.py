"""Course question import preview, readiness classification and atomic append."""
import hashlib
import json
import re
import uuid
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import log_action

from ..access import lock_writable_bank_content
from ..models import Concept, ImportPreviewBatch, Question, QuestionGroup, QuestionOption
from .import_parsing import QUESTION_TYPE_ALIASES, parse_question_kc_tree_rows, parse_question_rows
from .preview_batch import ImportPreviewBatchConflict, ImportPreviewBatchService
from .question_kc_mapping import replace_question_kc_mapping, validate_question_kc_mapping
from .question_readiness import content_issues, refresh_question_readiness
from .unified_reader import HEADERS, GROUP_HEADERS, read_unified_rows
from .question_groups import validate_group_structure, material_segments
from .numeric import normalize_finite_decimal


class SelectedRowIdsValidationError(ValueError):
    pass


def _text(value):
    return '' if value is None else str(value).replace('\r\n', '\n').replace('\r', '\n').strip()


def _messages(error):
    messages = error.messages if isinstance(error, ValidationError) else [str(error)]
    # The row parser is called once per record; its synthetic row is not provenance.
    return [re.sub(r'第 2 行(?:的)?', '', message).strip() for message in messages]


def _parse_content(raw):
    values = {key: _text(raw.get(key)) for key in HEADERS}
    educational_type = {'英汉互译': 'translation', '翻译题': 'translation', '翻译': 'translation', 'translation': 'translation', '作文题': 'essay', '作文': 'essay', '写作': 'essay', 'essay': 'essay'}.get(values['题型'], 'standard')
    if educational_type != 'standard':
        values['题型'] = 'short_answer'
    values['题型'] = {'阅读理解': 'single_choice', '选词填空': 'fill_blank', '短文填空': 'fill_blank'}.get(values['题型'], values['题型'])
    parsed = asdict(parse_question_rows(
        [{**values, '序号': '1'}],
        allow_incomplete=True,
    )[0])
    for key in ('sequence', 'raw_primary_kc', 'raw_secondary_kcs', 'raw_tertiary_kcs'):
        parsed.pop(key)
    parsed['educational_type'] = educational_type
    parsed['text'] = values['题目']
    parsed['options'] = {key: values[f'选项{key}'] for key in 'ABCDEF' if values[f'选项{key}']}
    parsed['explanation'] = values['解析']
    if parsed['question_type'] in (Question.Type.SHORT, Question.Type.FILL_BLANK):
        parsed['answer'] = values['答案']
    if parsed['question_type'] != Question.Type.MULTIPLE:
        parsed['accepted_answers'] = list(dict.fromkeys(
            answer.strip() for answer in values['可接受答案'].replace('；', ';').split(';') if answer.strip()
        ))
    parsed['title'] = '' if raw.get('题组编号') else _text(raw.get('材料标题'))
    parsed['instructions'] = '' if raw.get('题组编号') else _text(raw.get('作答说明'))
    if len(parsed['title']) > 240:
        raise ValueError('标题不能超过 240 个字符。')
    for field, column in (('absolute_tolerance', '绝对误差容限'), ('relative_tolerance', '相对误差容限')):
        parsed[field] = str(normalize_finite_decimal(raw[column])) if _text(raw.get(column)) else None
    maximum = normalize_finite_decimal(_text(raw.get('分值')) or '1')
    if maximum < Decimal('0.01') or maximum > Decimal('999.99') or maximum != maximum.quantize(Decimal('0.01')):
        raise ValueError('分值必须为 0.01 至 999.99，最多两位小数。')
    case_policy = _text(raw.get('大小写敏感')).casefold()
    if case_policy not in ('', 'false', 'true', '0', '1', '否', '是'):
        raise ValueError('大小写敏感必须填写 true/false、是/否或 1/0。')
    parsed.update(max_score=format(maximum, '.2f'), content_language=_text(raw.get('内容语言')) or 'und',
                  text_match_case_sensitive=case_policy in ('true', '1', '是'), text_match_policy_version='text-exact-v1')
    return parsed


def _parse_mapping(raw, bank):
    if bank.course_id is None:
        from .standalone_kc import parse_standalone_primary
        return parse_standalone_primary(raw, bank)
    tree = parse_question_kc_tree_rows([{'题目序号': '1', **{
        key: _text(raw.get(key)) for key in ('一级知识点', '二级知识点', '三级知识点')
    }}])[0]
    concepts = list(Concept.objects.filter(course=bank.course))

    def resolve(token):
        matches = [concept for concept in concepts if concept.code == token or concept.name == token]
        if len(matches) != 1:
            raise ValueError(f'知识点“{token}”在当前课程中不存在或名称不唯一，请填写准确的 KC 编码。')
        return matches[0]

    root = resolve(tree.root)
    nodes = [{'kc_code': root.code, 'name': root.name, 'parent_kc_code': None, 'order': 1}]
    second = [resolve(token) for token in tree.level_two]
    for order, concept in enumerate(second, 1):
        nodes.append({'kc_code': concept.code, 'name': concept.name, 'parent_kc_code': root.code, 'order': order})
    counts = {}
    for parent_token, child_token in tree.level_three:
        parent, child = resolve(parent_token), resolve(child_token)
        if parent.pk not in {concept.pk for concept in second}:
            raise ValueError(f'三级知识点的父节点“{parent_token}”未填写在二级知识点中。')
        counts[parent.code] = counts.get(parent.code, 0) + 1
        nodes.append({'kc_code': child.code, 'name': child.name, 'parent_kc_code': parent.code, 'order': counts[parent.code]})
    mapping = {'root_kc_code': root.code, 'nodes': nodes}
    _validate_mapping(bank, mapping)
    return mapping


def _validate_mapping(bank, mapping):
    if bank.course_id is None:
        from .standalone_kc import standalone_primary_mapping
        standalone_primary_mapping(bank, mapping['primary_concept_id'])
        return
    validate_question_kc_mapping(Question(bank=bank), **mapping)
    if Concept.objects.filter(course=bank.course, code__in=[node['kc_code'] for node in mapping['nodes']], children__isnull=False).exists():
        raise ValidationError('课程可用题目只能映射到叶节点 KC。')


def _replace_mapping(question, mapping):
    if question.bank.course_id is None:
        from .standalone_kc import replace_standalone_primary
        replace_standalone_primary(question, mapping['primary_concept_id'])
    else:
        replace_question_kc_mapping(question, **mapping)


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return str(Decimal(str(value)).normalize())
    except (InvalidOperation, TypeError, ValueError):
        return _text(value)


def _raw_kc_intent(raw):
    return {
        key: _text(raw.get(key))
        for key in ('一级知识点', '二级知识点', '三级知识点')
    }


def _unresolved_kc_intent(bank, raw):
    intent = _raw_kc_intent(raw)
    return None if bank.course_id is None and not any(intent.values()) else intent


def _fingerprint(content, mapping, *, raw_kc_intent=None):
    canonical = {**content, 'options': dict(sorted(content['options'].items())),
                 'accepted_answers': sorted(set(content['accepted_answers']))}
    canonical.pop('valid_cloze_context', None)
    # Keep the hash of legacy ordinary questions byte-for-byte compatible.
    for key, default in (('title', ''), ('instructions', ''), ('educational_type', 'standard'), ('max_score', '1'), ('content_language', 'und'), ('text_match_case_sensitive', False), ('text_match_policy_version', 'text-exact-v1')):
        value = canonical.get(key, default)
        if (key == 'max_score' and _decimal(value) == '1') or value == default:
            canonical.pop(key, None)
        elif key == 'max_score':
            canonical[key] = _decimal(value)
    for key in ('teacher_difficulty', 'absolute_tolerance', 'relative_tolerance'):
        canonical[key] = _decimal(canonical.get(key))
    if canonical['question_type'] == Question.Type.NUMERIC:
        canonical['answer'] = _decimal(canonical['answer'])
    if isinstance(canonical['answer'], list):
        canonical['answer'] = sorted(canonical['answer'])
    canonical['kc_mapping'] = None
    if mapping:
        if mapping.get('scope') == 'standalone':
            canonical['kc_mapping'] = {'scope': 'standalone', 'primary_concept_id': mapping['primary_concept_id'],
                'nodes': sorted([{key: node[key] for key in ('concept_id', 'is_primary', 'order')} for node in mapping['nodes']], key=lambda node: node['concept_id'])}
        else:
            canonical['kc_mapping'] = {
            'root_kc_code': mapping['root_kc_code'],
            'nodes': sorted([{key: node[key] for key in ('kc_code', 'parent_kc_code', 'order')}
                             for node in mapping['nodes']], key=lambda node: node['kc_code']),
        }
    elif raw_kc_intent is not None:
        canonical['kc_mapping'] = {'unresolved': raw_kc_intent}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _question_content(question):
    return {
        'text': question.text, 'title': question.title, 'instructions': question.instructions,
        'question_type': question.question_type, 'educational_type': question.educational_type,
        'answer': question.correct_answer,
        'accepted_answers': question.accepted_answers,
        'explanation': question.explanation,
        'teacher_difficulty': question.teacher_difficulty,
        'absolute_tolerance': question.absolute_tolerance,
        'relative_tolerance': question.relative_tolerance,
        'options': {option.key: option.text for option in question.options.all()},
        'max_score': str(question.max_score), 'content_language': question.content_language,
        'text_match_case_sensitive': question.text_match_case_sensitive, 'text_match_policy_version': question.text_match_policy_version,
    }


def _existing_fingerprints(bank):
    result = {}
    for question in bank.questions.filter(group__isnull=True).prefetch_related('options', 'concept_links__concept', 'concept_links__parent_link__concept'):
        links = list(question.concept_links.all())
        roots = [link.concept.code for link in links if link.is_primary]
        if len(roots) != 1 and bank.course_id is not None:
            continue
        mapping = {'root_kc_code': roots[0], 'nodes': [
            {'kc_code': link.concept.code, 'parent_kc_code': link.parent_link.concept.code if link.parent_link_id else None,
             'order': link.display_order} for link in links]} if roots else None
        if bank.course_id is None:
            from .standalone_kc import project_standalone_primary
            mapping = project_standalone_primary(question)
        result.setdefault(_fingerprint(_question_content(question), mapping), question.pk)
    return result


def _repairable_draft_fingerprints(bank):
    candidates = {}
    drafts = bank.questions.filter(status=Question.Status.DRAFT, group__isnull=True).prefetch_related(
        'options', 'concept_links',
    )
    for question in drafts:
        if question.concept_links.exists():
            continue
        fingerprint = _fingerprint(_question_content(question), None)
        candidates.setdefault(fingerprint, []).append(question.pk)
    return {
        fingerprint: question_ids[0]
        for fingerprint, question_ids in candidates.items()
        if len(question_ids) == 1
    }


def _project_row(row, ordinal, source_label):
    content = row.get('content') or {}
    options = content.get('options', {})
    question_type = content.get('question_type', '')
    return {'row_id': row['row_id'], 'sequence': ordinal, 'source_label': source_label,
            'text': content.get('text', ''), 'title': content.get('title', ''), 'instructions': content.get('instructions', ''), 'question_type': question_type,
            'educational_type': content.get('educational_type', 'standard'),
            'question_type_label': dict(Question.EducationalType.choices).get(content.get('educational_type')) if content.get('educational_type', 'standard') != 'standard' else dict(Question.Type.choices).get(question_type, '待修正'),
            'option_count': len(options), 'options': [{'key': key, 'text': text} for key, text in options.items()],
            'absolute_tolerance': content.get('absolute_tolerance'), 'relative_tolerance': content.get('relative_tolerance'),
            'teacher_difficulty': content.get('teacher_difficulty'), 'answer': content.get('answer'),
            'accepted_answers': content.get('accepted_answers', []), 'explanation': content.get('explanation', ''),
            'kc_mapping': row.get('kc_mapping'), 'classification': row['classification'],
            'errors': row['errors'], 'draft_reasons': row['errors'] if row['classification'] == 'draft' else [],
            'existing_question_id': row.get('existing_question_id'),
            'group_id': row.get('group_id'), 'group_order': row.get('group_order'), 'blank_id': row.get('blank_id', ''),
            'max_score': content.get('max_score', '1.00'), 'content_language': content.get('content_language', 'und'),
            'text_match_case_sensitive': content.get('text_match_case_sensitive', False),
            'text_match_policy_version': content.get('text_match_policy_version', 'text-exact-v1')}


def _selected_importable_row_ids(rows, selected_row_ids, *, allow_empty=False):
    if selected_row_ids is None:
        return None
    if not isinstance(selected_row_ids, list) or (not selected_row_ids and not allow_empty):
        raise SelectedRowIdsValidationError('请至少选择一道可导入题目。')
    try:
        selected_ids = [str(uuid.UUID(row_id)) for row_id in selected_row_ids]
    except (TypeError, ValueError, AttributeError) as error:
        raise SelectedRowIdsValidationError('所选预览行 ID 格式无效。') from error
    if len(selected_ids) != len(set(selected_ids)):
        raise SelectedRowIdsValidationError('不能重复选择同一预览行。')
    ready_ids = {
        row['row_id'] for row in rows if row['classification'] in {'ready', 'draft'}
    }
    if not set(selected_ids).issubset(ready_ids):
        raise SelectedRowIdsValidationError('所选预览行不存在或当前不可导入。')
    return set(selected_ids)


def _group_fingerprint(group, rows):
    members = []
    for row in sorted(rows, key=lambda item: item['group_order']):
        raw_intent = _raw_kc_intent(row.get('raw', {}))
        members.append({'order': row['group_order'], 'blank_id': row['blank_id'],
            'content': _fingerprint(row['content'], row.get('kc_mapping'),
                raw_kc_intent=raw_intent if any(raw_intent.values()) and not row.get('kc_mapping') else None)})
    canonical = {key: group[key] for key in ('kind', 'title', 'instructions', 'material', 'content_language', 'word_bank')}
    canonical['members'] = members
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _existing_group_fingerprints(bank):
    from .question_kc_mapping import project_question_kc_mapping
    result = {}
    for group in bank.groups.prefetch_related('members__options', 'members__concept_links__concept', 'members__concept_links__parent_link__concept'):
        rows = []
        for member in group.members.all():
            mapping = project_question_kc_mapping(member)
            if bank.course_id is None:
                from .standalone_kc import project_standalone_primary
                mapping = project_standalone_primary(member)
            rows.append({'group_order': member.group_order, 'blank_id': member.blank_id,
                'content': _question_content(member), 'kc_mapping': mapping if mapping and mapping['nodes'] else None})
        if rows:
            result[_group_fingerprint({key: getattr(group, key) for key in ('kind', 'title', 'instructions', 'material', 'content_language', 'word_bank')}, rows)] = group.pk
    return result


def _prepare_groups(bank, rows):
    grouped = {}
    for row in rows:
        code = row['raw'].get('题组编号', '')
        if code:
            grouped.setdefault(code, []).append(row)
        elif any(row['raw'].get(key) for key in ('题组形式', '共享材料', '共享词库', '组内顺序', '空位编号')):
            row['classification'] = 'invalid'
            row['errors'].append('题组字段必须填写题组编号。')
    seen = _existing_group_fingerprints(bank)
    groups = []
    for code, members in grouped.items():
        group = {'group_id': str(uuid.uuid4()), 'source_group_code': code, 'member_row_ids': [row['row_id'] for row in members],
                 'item_count': len(members), 'errors': [], 'material_segments': []}
        for column, field, default in (('题组形式', 'kind', ''), ('材料标题', 'title', ''), ('作答说明', 'instructions', ''),
                                        ('共享材料', 'material', ''), ('内容语言', 'content_language', 'en'), ('共享词库', 'word_bank', '')):
            values = {row['raw'].get(column, '') for row in members if row['raw'].get(column, '')}
            if len(values) > 1:
                group['errors'].append(f'题组“{code}”的{column}重复填写且内容冲突。')
            group[field] = next(iter(values), default)
        group['word_bank'] = [word.strip() for word in re.split(r'[|;；\n]', group['word_bank']) if word.strip()]
        group['kind'] = {'选词填空': 'words', '短文填空': 'cloze', '阅读理解': 'reading', '阅读': 'reading', '完形填空': 'cloze'}.get(group['kind'], group['kind'])
        case_policy = next((row['raw'].get('大小写敏感') for row in members if row['raw'].get('大小写敏感')), '')
        for row in members:
            if not row['raw'].get('大小写敏感') and case_policy:
                row['content']['text_match_case_sensitive'] = case_policy.casefold() in ('true', '1', '是')
            row['group_id'] = group['group_id']
            try:
                row['group_order'] = int(row['raw'].get('组内顺序', ''))
            except (TypeError, ValueError):
                row['group_order'] = None
            row['blank_id'] = row['raw'].get('空位编号', '')
            row['content']['content_language'] = group['content_language']
            if row['classification'] == 'invalid':
                group['errors'].extend(row['errors'])
        structural_members = [{**row['content'], 'item_key': row['row_id'], 'group_order': row['group_order'], 'blank_id': row['blank_id']} for row in members]
        try:
            validate_group_structure(group, structural_members)
        except ValidationError as error:
            group['errors'].extend(error.messages)
        if group['errors']:
            group['classification'] = 'invalid'
        else:
            group['material_segments'] = material_segments(group, structural_members)
            for row in members:
                row['content']['valid_cloze_context'] = group['kind'] in ('cloze', 'words')
                row['content_errors'] = content_issues(row['content'])
                row['errors'] = [*row['content_errors'], *row['mapping_errors']]
            fingerprint = _group_fingerprint(group, members)
            if fingerprint in seen:
                group.update(classification='duplicate', existing_group_id=seen[fingerprint])
            else:
                seen[fingerprint] = None
                group['classification'] = 'draft' if any(row['errors'] for row in members) else 'ready'
            group['fingerprint'] = fingerprint
            group['errors'] = list(dict.fromkeys(issue for row in members for issue in row['errors']))
        group['total_max_score'] = format(sum((Decimal(row['content'].get('max_score', '1')) for row in members), Decimal('0')), '.2f')
        for row in members:
            row['classification'] = group['classification']
            if group['classification'] == 'invalid':
                row['errors'] = list(dict.fromkeys([*row['errors'], *group['errors']]))
        groups.append(group)
    return groups


class UnifiedQuestionImportService:
    @staticmethod
    def preview(*, bank, actor, session_key, uploaded):
        if uploaded.size > 8 * 1024 * 1024:
            raise ValueError('文件不能超过 8 MB。')
        raw_bytes = uploaded.read()
        uploaded.seek(0)
        source = read_unified_rows(uploaded, allow_legacy=bank.course_id is None)
        legacy_sequences = None
        if bank.course_id is None and any(raw.get('_legacy_standalone') for raw in source):
            sequence_floor = bank.questions.aggregate(value=Max('sequence'))['value'] or 0
            legacy_sequences = [row.sequence for row in parse_question_rows(source, sequence_floor=sequence_floor, allow_incomplete=True)]
            conflicts = list(bank.questions.filter(sequence__in=legacy_sequences).values_list('sequence', flat=True))
            if conflicts:
                raise ValueError(f'题库中已存在以下序号：{conflicts}。请先修改序号或编辑现有题目。')
        rows, projected = [], []
        seen = _existing_fingerprints(bank)
        repairable_drafts = _repairable_draft_fingerprints(bank)
        summary = {'ready': 0, 'draft': 0, 'duplicate': 0, 'invalid': 0}
        for ordinal, raw in enumerate(source, 1):
            invalid_errors = list(raw.get('_errors', []))
            content_errors = []
            mapping_errors = []
            row = {
                'row_id': str(uuid.uuid4()),
                'raw': {key: _text(raw.get(key)) for key in GROUP_HEADERS if key != '序号'},
                'classification': 'ready',
            }
            if legacy_sequences is not None:
                row['legacy_sequence'] = legacy_sequences[ordinal - 1]
            try:
                row['content'] = _parse_content(raw)
            except (ValueError, ValidationError) as error:
                invalid_errors.extend(_messages(error))
                row['content'] = {
                    'text': _text(raw.get('题目')),
                    'question_type': QUESTION_TYPE_ALIASES.get(_text(raw.get('题型')).casefold(), ''),
                    'options': {key: _text(raw.get(f'选项{key}')) for key in 'ABCDEF' if _text(raw.get(f'选项{key}'))},
                    'answer': _text(raw.get('答案')),
                    'accepted_answers': [_text(answer) for answer in _text(raw.get('可接受答案')).replace('；', ';').split(';') if _text(answer)],
                    'explanation': _text(raw.get('解析')),
                    'teacher_difficulty': _text(raw.get('难度')) or None,
                }
            else:
                content_errors.extend(content_issues(row['content']))
            try:
                row['kc_mapping'] = _parse_mapping(raw, bank)
            except (ValueError, ValidationError) as error:
                row['kc_mapping'] = None
                mapping_errors.extend(_messages(error))
            row['content_errors'] = content_errors
            row['mapping_errors'] = mapping_errors
            row['errors'] = [*invalid_errors, *content_errors, *mapping_errors]
            if invalid_errors:
                row['classification'] = 'invalid'
            else:
                fingerprint = _fingerprint(
                    row['content'],
                    row['kc_mapping'],
                    raw_kc_intent=(
                        None
                        if row['kc_mapping']
                        else _unresolved_kc_intent(bank, row['raw'])
                    ),
                )
                if raw.get('题组编号'):
                    if row['errors']:
                        row['classification'] = 'draft'
                elif fingerprint in seen:
                    row.update(classification='duplicate', existing_question_id=seen[fingerprint])
                else:
                    seen[fingerprint] = None
                    if row['errors']:
                        row['classification'] = 'draft'
                    elif row['kc_mapping']:
                        row['existing_question_id'] = repairable_drafts.get(
                            _fingerprint(row['content'], None),
                        )
            rows.append(row)
        groups = _prepare_groups(bank, rows)
        for ordinal, (row, raw) in enumerate(zip(rows, source), 1):
            summary[row['classification']] += 1
            projected.append(_project_row(
                row,
                ordinal,
                raw.get('_location', f'第 {ordinal} 条'),
            ))
        batch = ImportPreviewBatchService.create(
            kind=ImportPreviewBatch.Kind.QUESTION_CONTENT, course=bank.course, bank=bank, actor=actor,
            session_key=session_key, file_bytes=raw_bytes, payload={'rows': rows, 'groups': groups}, summary=summary,
        )
        return {
            'preview_id': str(batch.pk),
            'preview': {
                'rows': projected,
                'groups': groups,
                'summary': summary,
                'confirmable': summary['ready'] + summary['draft'] > 0,
            },
        }

    @staticmethod
    @transaction.atomic
    def confirm(*, bank, actor, session_key, preview_id, selected_row_ids=None, selected_group_ids=None, audit_request=None):
        bank = lock_writable_bank_content(bank)
        course = bank.course
        batch = ImportPreviewBatchService._locked_valid_batch(
            batch_id=preview_id, kind=ImportPreviewBatch.Kind.QUESTION_CONTENT, course=course, bank=bank,
            actor=actor, session_key=session_key, allow_consumed=True,
        )
        groups = batch.payload.get('groups', [])
        ordinary_rows = [row for row in batch.payload['rows'] if not row.get('group_id')]
        if groups:
            if selected_row_ids is None and selected_group_ids is None:
                selected_ids = {row['row_id'] for row in ordinary_rows if row['classification'] in ('ready', 'draft')}
                selected_groups = {group['group_id'] for group in groups if group['classification'] in ('ready', 'draft')}
            else:
                selected_ids = _selected_importable_row_ids(ordinary_rows, [] if selected_row_ids is None else selected_row_ids, allow_empty=True)
                selected_groups = _selected_importable_row_ids([
                    {'row_id': group['group_id'], 'classification': group['classification']} for group in groups
                ], [] if selected_group_ids is None else selected_group_ids, allow_empty=True)
            selection = {'rows': sorted(selected_ids), 'groups': sorted(selected_groups)}
            if batch.status == ImportPreviewBatch.Status.CONSUMED:
                if batch.payload.get('_selection') != selection:
                    raise ImportPreviewBatchConflict('preview_selection_mismatch', '该预览已经使用其他选择确认，请重新上传。')
                return batch.payload['result']
            if not selected_ids and not selected_groups and (selected_row_ids is not None or selected_group_ids is not None or not any(group['classification'] == 'duplicate' for group in groups)):
                raise SelectedRowIdsValidationError('请至少选择一道普通题或一个完整题组。')
        else:
            if selected_group_ids:
                raise SelectedRowIdsValidationError('当前预览不存在可选择的题组。')
            if batch.status == ImportPreviewBatch.Status.CONSUMED:
                return batch.payload['result']
            selected_ids = _selected_importable_row_ids(ordinary_rows, selected_row_ids)
            selected_groups, selection = set(), None
        seen = _existing_fingerprints(bank)
        existing_groups = _existing_group_fingerprints(bank)
        saved_groups, duplicate_groups = {}, {}
        for group in groups:
            if group['group_id'] not in selected_groups:
                continue
            if group['fingerprint'] in existing_groups:
                duplicate_groups[group['group_id']] = existing_groups[group['fingerprint']]
                continue
            saved_groups[group['group_id']] = QuestionGroup.objects.create(bank=bank,
                **{key: group[key] for key in ('kind', 'title', 'instructions', 'material', 'content_language', 'word_bank')})
        for row in ordinary_rows:
            if row.get('legacy_sequence') and (selected_ids is None or row['row_id'] in selected_ids) and bank.questions.filter(sequence=row['legacy_sequence']).exists():
                raise ImportPreviewBatchConflict('question_import_preview_stale', '导入预览已失效：题库中已存在文件指定序号，请重新上传。')
        sequence = bank.questions.aggregate(value=Max('sequence'))['value'] or 0
        result = {'created': 0, 'updated': 0, 'active': 0, 'draft': 0, 'skipped': 0, 'failed': 0,
                  'detail_url': reverse('courses:bank_detail', args=[course.pk, bank.pk]) if course else reverse('question_bank:bank_detail', args=[bank.pk]), 'rows': [], 'groups': []}
        grouped_created = []
        for row in batch.payload['rows']:
            outcome = {'row_id': row['row_id'], 'status': 'invalid', 'errors': list(row['errors'])}
            result['rows'].append(outcome)
            group_id = row.get('group_id')
            if group_id and group_id not in selected_groups and row['classification'] in ('ready', 'draft'):
                outcome['status'] = 'unselected'
                continue
            if group_id and (group_id in duplicate_groups or row['classification'] == 'duplicate'):
                outcome.update(status='duplicate', group_id=duplicate_groups.get(group_id))
                result['skipped'] += 1
                continue
            if (not group_id and
                selected_ids is not None
                and row['classification'] in {'ready', 'draft'}
                and row['row_id'] not in selected_ids
            ):
                outcome['status'] = 'unselected'
                continue
            if row['classification'] == 'invalid':
                result['failed'] += 1
                continue

            content = row['content']
            draft_reasons = content_issues(content)
            mapping = row.get('kc_mapping')
            mapping_is_valid = False
            if mapping:
                try:
                    _validate_mapping(bank, mapping)
                except (ValueError, ValidationError) as error:
                    draft_reasons.extend(_messages(error))
                    mapping = None
                else:
                    mapping_is_valid = True
            elif bank.course_id is not None:
                draft_reasons.extend(row.get('mapping_errors') or ['题目缺少有效的课程 KC 映射。'])
            draft_reasons = list(dict.fromkeys(draft_reasons))
            raw_kc_intent = None if mapping_is_valid else _unresolved_kc_intent(bank, row['raw'])
            fingerprint = _fingerprint(
                content,
                mapping if mapping_is_valid else None,
                raw_kc_intent=raw_kc_intent,
            )
            if not group_id and fingerprint in seen:
                outcome.update(status='duplicate', question_id=seen[fingerprint])
                result['skipped'] += 1
                continue
            if selected_ids is not None and row['classification'] == 'duplicate':
                outcome.update(status='duplicate', question_id=row.get('existing_question_id'))
                result['skipped'] += 1
                continue
            repair_question_id = row.get('existing_question_id') if not group_id and mapping_is_valid and not draft_reasons else None
            if repair_question_id is not None:
                question = (
                    bank.questions.select_for_update()
                    .filter(pk=repair_question_id, status=Question.Status.DRAFT)
                    .prefetch_related('options', 'concept_links')
                    .first()
                )
                if (
                    question is not None
                    and not question.concept_links.exists()
                    and _fingerprint(_question_content(question), None) == _fingerprint(content, None)
                ):
                    _replace_mapping(question, mapping)
                    readiness_errors = refresh_question_readiness(question)
                    outcome.update(
                        status='updated',
                        question_id=question.pk,
                        question_status=question.status,
                        draft_reasons=(
                            readiness_errors if question.status == Question.Status.DRAFT else []
                        ),
                    )
                    result['updated'] += 1
                    result[question.status] += 1
                    seen[fingerprint] = question.pk
                    continue
            sequence = max(sequence + 1, row.get('legacy_sequence', 0))
            question = Question.objects.create(
                bank=bank, sequence=row.get('legacy_sequence', sequence), status=Question.Status.DRAFT, text=content['text'],
                title=content.get('title', ''), instructions=content.get('instructions', ''),
                question_type=content['question_type'], correct_answer=content['answer'],
                accepted_answers=content['accepted_answers'], explanation=content['explanation'],
                teacher_difficulty=content['teacher_difficulty'],
                absolute_tolerance=content['absolute_tolerance'], relative_tolerance=content['relative_tolerance'],
                group=saved_groups.get(group_id), group_order=row.get('group_order'), blank_id=row.get('blank_id', ''),
                educational_type=content.get('educational_type', 'standard'), max_score=content.get('max_score', '1'), content_language=content.get('content_language', 'und'),
                text_match_case_sensitive=content.get('text_match_case_sensitive', False), text_match_policy_version=content.get('text_match_policy_version', 'text-exact-v1'),
            )
            QuestionOption.objects.bulk_create([
                QuestionOption(question=question, key=key, text=text, display_order=order)
                for order, (key, text) in enumerate(content['options'].items(), 1)
            ])
            if mapping_is_valid:
                try:
                    _replace_mapping(question, mapping)
                except (ValueError, ValidationError) as error:
                    mapping_is_valid = False
                    mapping = None
                    draft_reasons.extend(_messages(error))
            readiness_errors = refresh_question_readiness(question)
            draft_reasons = list(dict.fromkeys([*draft_reasons, *readiness_errors]))
            seen[_fingerprint(
                content,
                mapping if mapping_is_valid else None,
                raw_kc_intent=(
                    None if mapping_is_valid else _unresolved_kc_intent(bank, row['raw'])
                ),
            )] = question.pk
            outcome.update(
                status='created',
                question_id=question.pk,
                question_status=question.status,
                draft_reasons=draft_reasons if question.status == Question.Status.DRAFT else [],
            )
            result['created'] += 1
            result[question.status] += 1
            if group_id:
                grouped_created.append((question, outcome))
        for question, outcome in grouped_created:
            result[outcome['question_status']] -= 1
            errors = refresh_question_readiness(question)
            result[question.status] += 1
            outcome.update(question_status=question.status, draft_reasons=errors if question.status == Question.Status.DRAFT else [])
        for group in groups:
            saved = saved_groups.get(group['group_id'])
            result['groups'].append({'group_id': group['group_id'], 'question_group_id': saved.pk if saved else duplicate_groups.get(group['group_id']),
                'status': 'created' if saved else 'duplicate' if group['classification'] == 'duplicate' or group['group_id'] in duplicate_groups else 'invalid' if group['classification'] == 'invalid' else 'unselected'})
        batch.status = ImportPreviewBatch.Status.CONSUMED
        batch.consumed_at = timezone.now()
        batch.payload['result'] = result
        if selection is not None:
            batch.payload['_selection'] = selection
        batch.save(update_fields=['status', 'consumed_at', 'payload'])
        if audit_request is not None:
            log_action(audit_request, 'question.import', bank,
                       {'course_id': course.pk if course else None, 'batch_id': str(batch.pk),
                        'count': result['created'] + result['updated'],
                        'created': result['created'], 'updated': result['updated'],
                        'skipped': result['skipped'], 'failed': result['failed']})
        return result
