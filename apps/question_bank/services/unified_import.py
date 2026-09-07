"""Course question import: validate complete records, then atomically append."""
import hashlib
import json
import re
import uuid
from dataclasses import asdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import log_action

from ..access import lock_writable_course_bank
from ..models import Concept, ImportPreviewBatch, Question, QuestionOption
from .import_parsing import QUESTION_TYPE_ALIASES, parse_question_kc_tree_rows, parse_question_rows
from .preview_batch import ImportPreviewBatchService
from .question_kc_mapping import replace_question_kc_mapping, validate_question_kc_mapping
from .unified_reader import HEADERS, read_unified_rows


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
    parsed = asdict(parse_question_rows([{**values, '序号': '1'}])[0])
    parsed.pop('sequence')
    parsed['text'] = values['题目']
    parsed['options'] = {key: values[f'选项{key}'] for key in 'ABCDEF' if values[f'选项{key}']}
    parsed['explanation'] = values['解析']
    if parsed['question_type'] in (Question.Type.SHORT, Question.Type.FILL_BLANK):
        parsed['answer'] = values['答案']
    if parsed['question_type'] != Question.Type.MULTIPLE:
        parsed['accepted_answers'] = list(dict.fromkeys(
            answer.strip() for answer in values['可接受答案'].replace('；', ';').split(';') if answer.strip()
        ))
    parsed['absolute_tolerance'] = None
    parsed['relative_tolerance'] = None
    return parsed


def _parse_mapping(raw, bank):
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
    validate_question_kc_mapping(Question(bank=bank), **mapping)
    if Concept.objects.filter(course=bank.course, code__in=[node['kc_code'] for node in mapping['nodes']], children__isnull=False).exists():
        raise ValidationError('课程可用题目只能映射到叶节点 KC。')


def _decimal(value):
    return None if value in (None, '') else str(Decimal(str(value)).normalize())


def _fingerprint(content, mapping):
    canonical = {**content, 'options': dict(sorted(content['options'].items())),
                 'accepted_answers': sorted(set(content['accepted_answers']))}
    for key in ('teacher_difficulty', 'absolute_tolerance', 'relative_tolerance'):
        canonical[key] = _decimal(canonical.get(key))
    if canonical['question_type'] == Question.Type.NUMERIC:
        canonical['answer'] = _decimal(canonical['answer'])
    if isinstance(canonical['answer'], list):
        canonical['answer'] = sorted(canonical['answer'])
    canonical['kc_mapping'] = {
        'root_kc_code': mapping['root_kc_code'],
        'nodes': sorted([{key: node[key] for key in ('kc_code', 'parent_kc_code', 'order')}
                         for node in mapping['nodes']], key=lambda node: node['kc_code']),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _existing_fingerprints(bank):
    result = {}
    for question in bank.questions.prefetch_related('options', 'concept_links__concept', 'concept_links__parent_link__concept'):
        links = list(question.concept_links.all())
        roots = [link.concept.code for link in links if link.is_primary]
        if len(roots) != 1:
            continue
        mapping = {'root_kc_code': roots[0], 'nodes': [
            {'kc_code': link.concept.code, 'parent_kc_code': link.parent_link.concept.code if link.parent_link_id else None,
             'order': link.display_order} for link in links]}
        content = {'text': question.text, 'question_type': question.question_type,
                   'answer': question.correct_answer, 'accepted_answers': question.accepted_answers,
                   'explanation': question.explanation, 'teacher_difficulty': question.teacher_difficulty,
                   'absolute_tolerance': question.absolute_tolerance, 'relative_tolerance': question.relative_tolerance,
                   'options': {option.key: option.text for option in question.options.all()}}
        result.setdefault(_fingerprint(content, mapping), question.pk)
    return result


def _project_row(row, ordinal, source_label):
    content = row.get('content') or {}
    options = content.get('options', {})
    question_type = content.get('question_type', '')
    return {'row_id': row['row_id'], 'sequence': ordinal, 'source_label': source_label,
            'text': content.get('text', ''), 'question_type': question_type,
            'question_type_label': dict(Question.Type.choices).get(question_type, '待修正'),
            'option_count': len(options), 'options': [{'key': key, 'text': text} for key, text in options.items()],
            'teacher_difficulty': content.get('teacher_difficulty'), 'answer': content.get('answer'),
            'accepted_answers': content.get('accepted_answers', []), 'explanation': content.get('explanation', ''),
            'kc_mapping': row.get('kc_mapping'), 'classification': row['classification'],
            'errors': row['errors'], 'existing_question_id': row.get('existing_question_id')}


def _selected_ready_row_ids(rows, selected_row_ids):
    if selected_row_ids is None:
        return None
    if not isinstance(selected_row_ids, list) or not selected_row_ids:
        raise SelectedRowIdsValidationError('请至少选择一道可导入题目。')
    try:
        selected_ids = [str(uuid.UUID(row_id)) for row_id in selected_row_ids]
    except (TypeError, ValueError, AttributeError) as error:
        raise SelectedRowIdsValidationError('所选预览行 ID 格式无效。') from error
    if len(selected_ids) != len(set(selected_ids)):
        raise SelectedRowIdsValidationError('不能重复选择同一预览行。')
    ready_ids = {
        row['row_id'] for row in rows if row['classification'] == 'ready'
    }
    if not set(selected_ids).issubset(ready_ids):
        raise SelectedRowIdsValidationError('所选预览行不存在或当前不可导入。')
    return set(selected_ids)


class UnifiedQuestionImportService:
    @staticmethod
    def preview(*, bank, actor, session_key, uploaded):
        if uploaded.size > 8 * 1024 * 1024:
            raise ValueError('文件不能超过 8 MB。')
        raw_bytes = uploaded.read()
        uploaded.seek(0)
        source = read_unified_rows(uploaded)
        rows, projected = [], []
        seen = _existing_fingerprints(bank)
        summary = {'ready': 0, 'duplicate': 0, 'invalid': 0}
        for ordinal, raw in enumerate(source, 1):
            row = {'row_id': str(uuid.uuid4()), 'errors': list(raw.get('_errors', [])), 'classification': 'ready'}
            try:
                row['content'] = _parse_content(raw)
            except (ValueError, ValidationError) as error:
                row['errors'].extend(_messages(error))
                row['content'] = {
                    'text': _text(raw.get('题目')),
                    'question_type': QUESTION_TYPE_ALIASES.get(_text(raw.get('题型')).casefold(), ''),
                    'options': {key: _text(raw.get(f'选项{key}')) for key in 'ABCDEF' if _text(raw.get(f'选项{key}'))},
                    'answer': _text(raw.get('答案')),
                    'accepted_answers': [_text(answer) for answer in _text(raw.get('可接受答案')).replace('；', ';').split(';') if _text(answer)],
                    'explanation': _text(raw.get('解析')),
                    'teacher_difficulty': _text(raw.get('难度')) or None,
                }
            try:
                row['kc_mapping'] = _parse_mapping(raw, bank)
            except (ValueError, ValidationError) as error:
                row['errors'].extend(_messages(error))
            if row['errors']:
                row['classification'] = 'invalid'
            else:
                fingerprint = _fingerprint(row['content'], row['kc_mapping'])
                if fingerprint in seen:
                    row.update(classification='duplicate', existing_question_id=seen[fingerprint])
                else:
                    seen[fingerprint] = None
            rows.append(row)
            summary[row['classification']] += 1
            projected.append(_project_row(row, ordinal, raw.get('_location', f'第 {ordinal} 条')))
        batch = ImportPreviewBatchService.create(
            kind=ImportPreviewBatch.Kind.QUESTION_CONTENT, course=bank.course, bank=bank, actor=actor,
            session_key=session_key, file_bytes=raw_bytes, payload={'rows': rows}, summary=summary,
        )
        return {'preview_id': str(batch.pk), 'preview': {'rows': projected, 'summary': summary, 'confirmable': summary['ready'] > 0}}

    @staticmethod
    @transaction.atomic
    def confirm(*, bank, actor, session_key, preview_id, selected_row_ids=None, audit_request=None):
        course, bank = lock_writable_course_bank(bank.course, bank, content=True)
        batch = ImportPreviewBatchService._locked_valid_batch(
            batch_id=preview_id, kind=ImportPreviewBatch.Kind.QUESTION_CONTENT, course=course, bank=bank,
            actor=actor, session_key=session_key, allow_consumed=True,
        )
        if batch.status == ImportPreviewBatch.Status.CONSUMED:
            return batch.payload['result']
        selected_ids = _selected_ready_row_ids(
            batch.payload['rows'], selected_row_ids,
        )
        seen = _existing_fingerprints(bank)
        sequence = bank.questions.aggregate(value=Max('sequence'))['value'] or 0
        result = {'created': 0, 'skipped': 0, 'failed': 0,
                  'detail_url': reverse('courses:bank_detail', args=[course.pk, bank.pk]), 'rows': []}
        for row in batch.payload['rows']:
            outcome = {'row_id': row['row_id'], 'status': 'invalid', 'errors': list(row['errors'])}
            result['rows'].append(outcome)
            if (
                selected_ids is not None
                and row['classification'] == 'ready'
                and row['row_id'] not in selected_ids
            ):
                outcome['status'] = 'unselected'
                continue
            if not outcome['errors']:
                try:
                    _validate_mapping(bank, row['kc_mapping'])
                except (ValueError, ValidationError) as error:
                    outcome['errors'].extend(_messages(error))
            if outcome['errors']:
                result['failed'] += 1
                continue
            content, mapping = row['content'], row['kc_mapping']
            fingerprint = _fingerprint(content, mapping)
            if fingerprint in seen:
                outcome.update(status='duplicate', question_id=seen[fingerprint])
                result['skipped'] += 1
                continue
            if selected_ids is not None and row['classification'] == 'duplicate':
                outcome.update(status='duplicate', question_id=row.get('existing_question_id'))
                result['skipped'] += 1
                continue
            sequence += 1
            question = Question.objects.create(
                bank=bank, sequence=sequence, status=Question.Status.DRAFT, text=content['text'],
                question_type=content['question_type'], correct_answer=content['answer'],
                accepted_answers=content['accepted_answers'], explanation=content['explanation'],
                teacher_difficulty=content['teacher_difficulty'],
                absolute_tolerance=content['absolute_tolerance'], relative_tolerance=content['relative_tolerance'],
            )
            QuestionOption.objects.bulk_create([
                QuestionOption(question=question, key=key, text=text, display_order=order)
                for order, (key, text) in enumerate(content['options'].items(), 1)
            ])
            replace_question_kc_mapping(question, **mapping)
            question.status = Question.Status.ACTIVE
            question.save(update_fields=['status'])
            seen[fingerprint] = question.pk
            outcome.update(status='created', question_id=question.pk)
            result['created'] += 1
        batch.status = ImportPreviewBatch.Status.CONSUMED
        batch.consumed_at = timezone.now()
        batch.payload['result'] = result
        batch.save(update_fields=['status', 'consumed_at', 'payload'])
        if audit_request is not None:
            log_action(audit_request, 'question.import', bank,
                       {'course_id': course.pk, 'batch_id': str(batch.pk),
                        'count': result['created'], 'skipped': result['skipped'], 'failed': result['failed']})
        return result
