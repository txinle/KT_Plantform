"""Frozen, optional item tools and their observable use; no inferred ability score."""
import hashlib
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .capture_v2_services import uses_capture_v2
from .deadline_services import DeadlineDecisionService
from .event_services import append_assessment_event
from .models import AssessmentEvent, Attempt, ExperimentSession


def tool_configuration(attempt):
    return attempt.session.version.configuration.get('research_tools', {}) if uses_capture_v2(attempt.session) else {}


def content_version(attempt):
    return hashlib.sha256(json.dumps(tool_configuration(attempt), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _hint(attempt, hint_id):
    return next((row for row in tool_configuration(attempt).get('hints', [])
                 if row['id'] == hint_id and row['question_code'] == attempt.assignment.snapshot.code), None)


def _assert_tool(attempt, tool, data):
    config = tool_configuration(attempt)
    if tool in {'doubt', 'check'} and not config.get(f'{tool}_enabled', False):
        raise ValidationError('该实验未提供此工具。')
    if tool == 'hint':
        hint = _hint(attempt, data.get('hint_id'))
        if hint is None:
            raise ValidationError('该题未提供此提示。')
        if hint['contains_answer']:
            from .confidence_services import frozen_feedback_payload
            if 'correct_answer' not in frozen_feedback_payload(attempt):
                raise ValidationError('此提示受答案反馈权限限制。')
    elif tool == 'confidence':
        from .confidence_services import ensure_confidence_assignment
        assignment = ensure_confidence_assignment(attempt)
        if assignment.condition != assignment.Condition.PROMPT:
            raise ValidationError('该题未分配信心评价。')
    elif tool not in {'doubt', 'check'}:
        raise ValidationError('不支持的研究工具。')


def available_tools(attempt):
    from .confidence_services import confidence_scale_contract

    config = tool_configuration(attempt)
    state = {'doubt': False, 'pending_check': False, 'checking': False, 'doubt_note': ''}
    for row in AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool').order_by('server_ingest_sequence_no'):
        tool, stage = row.payload['tool'], row.payload['stage']
        if tool in {'doubt', 'check'} and stage in {'mark_added', 'mark_changed', 'mark_removed'}:
            state['doubt' if tool == 'doubt' else 'pending_check'] = stage != 'mark_removed'
            if tool == 'doubt':
                state['doubt_note'] = row.payload['data'].get('note', '')
        if tool == 'check' and stage in {'started', 'finished'}:
            state['checking'] = stage == 'started'
    result = {
        'state': state,
        'doubt_enabled': config.get('doubt_enabled', False),
        'check_enabled': config.get('check_enabled', False),
        'kc_visible': config.get('kc_visible', True),
        'content_version': content_version(attempt),
        'hints': [{'id': hint['id'], 'level': hint['level']} for hint in config.get('hints', [])
                  if hint['question_code'] == attempt.assignment.snapshot.code and not hint['contains_answer']],
    }
    if any(rule.get('tool') == 'confidence' for rule in config.get('rules', [])):
        result['confidence_scale'] = confidence_scale_contract(attempt)
    return result


def _response_reference(attempt, event_id):
    if event_id is None:
        return None, None
    event = AssessmentEvent.objects.filter(pk=event_id, attempt=attempt, event_type='response_transition').first()
    if event is None:
        raise ValidationError('工具所关联的答案状态尚未上传。')
    return str(event.pk), event.payload['after_snapshot_id']


@transaction.atomic
def record_tool_action(*, student, attempt_id, tool, stage, operation_id, data=None,
                       answer_event_id=None, initiation_type='self_initiated'):
    data = data or {}
    session_id = Attempt.objects.filter(pk=attempt_id, session__student=student).values_list('session_id', flat=True).first()
    if session_id is None:
        raise ValidationError('无权访问此作答。')
    from apps.experiments.models import ClassExperimentTask
    task_id = ExperimentSession.objects.values_list('class_task_id', flat=True).get(pk=session_id)
    if task_id:
        ClassExperimentTask.objects.select_for_update().get(pk=task_id)
    session = ExperimentSession.objects.select_for_update().get(pk=session_id)
    attempt = Attempt.objects.select_for_update().select_related('assignment__snapshot', 'session__version').get(pk=attempt_id)
    try:
        event_id = uuid.UUID(str(operation_id))
    except (ValueError, TypeError):
        raise ValidationError('行为标识无效。') from None
    answer_event_id, snapshot_id = _response_reference(attempt, answer_event_id)
    payload = {'capture_version': 'capture-v2', 'operation_id': operation_id,
               'tool': tool, 'stage': stage, 'initiation_type': initiation_type,
               'answer_event_id': answer_event_id, 'snapshot_id': snapshot_id,
               'content_version': content_version(attempt), 'data': data}
    prior = AssessmentEvent.objects.filter(pk=event_id).first()
    if prior:
        if prior.attempt_id != attempt.pk or prior.event_type != 'research_tool' or prior.payload != payload:
            raise ValidationError('行为标识冲突。')
        return _tool_result(attempt, prior)
    _assert_tool(attempt, tool, data)
    stages = {'doubt': {'mark_added', 'mark_changed', 'mark_removed'},
              'check': {'requested', 'presented', 'mark_added', 'mark_removed', 'started', 'finished', 'responded'},
              'hint': {'requested', 'presented', 'closed', 'responded'},
              'confidence': {'requested', 'presented', 'responded'}}
    if stage not in stages.get(tool, set()):
        raise ValidationError('无效的工具行为。')
    if stage in {'requested', 'mark_added', 'mark_changed', 'mark_removed', 'started', 'finished', 'responded'}:
        if attempt.status in {'submitted', 'timed_out', 'unanswered'} or session.status != 'in_progress':
            raise ValidationError('正式作答已经结束。')
        if session.class_task_id:
            decision = DeadlineDecisionService.can_write_response(session.class_task, session, attempt, timezone.now())
            if not decision.allowed:
                raise ValidationError(decision.code)
        else:
            deadline = DeadlineDecisionService.get_item_deadline(attempt)
            if deadline and timezone.now() >= deadline:
                raise ValidationError('作答时间已结束。')
    if initiation_type != 'self_initiated':
        rule_id = data.get('rule_id')
        rule = next((rule for rule in tool_configuration(attempt).get('rules', []) if rule['id'] == rule_id), None)
        if rule is None or rule['tool'] != tool or initiation_type != ('task_required' if rule['required'] else 'system_prompted'):
            raise ValidationError('规则来源无效。')
        if not any(row['id'] == rule_id for row in eligible_rules(attempt)):
            raise ValidationError('该规则尚未触发。')
        if stage == 'requested' and AssessmentEvent.objects.filter(attempt=attempt,
            event_type='research_tool', payload__stage='requested', payload__data__rule_id=rule_id).count() >= rule['max_exposures']:
            raise ValidationError('该规则已达到出现上限。')
    if tool == 'confidence':
        from .confidence_services import (
            commit_confidence,
            mark_confidence_prompt_shown,
            validate_confidence_probability,
        )
        if stage == 'presented':
            mark_confidence_prompt_shown(attempt_id=attempt.pk, student=student)
        if stage == 'responded':
            probability = data.get('probability')
            validate_confidence_probability(attempt, probability)
            from .confidence_services import ensure_confidence_assignment
            assignment = ensure_confidence_assignment(attempt)
            if assignment.response_status != assignment.ResponseStatus.COMMITTED:
                commit_confidence(attempt_id=attempt.pk, student=student, probability=probability)
    event = append_assessment_event(event_type='research_tool', event_version=1, event_domain='assessment',
        actor_source='server', session=session, attempt=attempt, payload=payload, event_id=event_id, app_version='capture-v2')
    return _tool_result(attempt, event)


def _tool_result(attempt, event):
    result = {'event_id': str(event.pk), 'content_version': event.payload['content_version']}
    if event.payload['tool'] == 'hint' and event.payload['stage'] == 'requested':
        result['text'] = _hint(attempt, event.payload['data'].get('hint_id'))['text']
    return result


def eligible_rules(attempt):
    elapsed = max(0, (timezone.now() - attempt.session.started_at).total_seconds())
    result = []
    for rule in tool_configuration(attempt).get('rules', []):
        if rule['tool'] == 'confidence':
            from .confidence_services import ensure_confidence_assignment
            assignment = ensure_confidence_assignment(attempt)
            if assignment.condition != assignment.Condition.PROMPT:
                continue
        if rule['question_code'] and rule['question_code'] != attempt.assignment.snapshot.code:
            continue
        if rule['trigger'] == 'elapsed_seconds' and elapsed < rule['after_seconds']:
            continue
        result.append(rule)
    return result


def due_rules(attempt):
    if attempt.status in {'submitted', 'timed_out', 'unanswered'} or attempt.session.status != 'in_progress':
        return []
    result = []
    for rule in eligible_rules(attempt):
        events = AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool', payload__data__rule_id=rule['id'])
        latest_request = events.filter(payload__stage='requested').order_by('-server_ingest_sequence_no').first()
        pending = latest_request and not events.filter(server_ingest_sequence_no__gt=latest_request.server_ingest_sequence_no,
            payload__stage__in=['responded', 'finished']).exists()
        if pending:
            result.append({**rule, 'pending_request': _tool_result(attempt, latest_request),
                'already_presented': events.filter(server_ingest_sequence_no__gt=latest_request.server_ingest_sequence_no,
                    payload__stage='presented').exists()})
        elif events.filter(payload__stage='requested').count() < rule['max_exposures']:
            result.append(rule)
    return result


def assert_required_tools(attempt):
    for rule in eligible_rules(attempt):
        if not rule['required']:
            continue
        if not AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool',
            payload__data__rule_id=rule['id'], payload__stage__in=['responded', 'finished']).exists():
            raise ValidationError('请先完成本题要求的信心或检查操作。')


def prepare_tool_observation(parsed):
    payload = dict(parsed.payload)
    if not uses_capture_v2(parsed.session) or payload['content_version'] != content_version(parsed.attempt):
        raise ValidationError('工具采集版本不匹配。')
    _assert_tool(parsed.attempt, payload['tool'], payload['data'])
    source = AssessmentEvent.objects.filter(pk=payload['server_event_id'], attempt=parsed.attempt,
        event_type='research_tool').first()
    if source is None or source.payload['tool'] != payload['tool']:
        raise ValidationError('工具呈现缺少已授权请求。')
    if source.payload['initiation_type'] != payload['initiation_type'] or source.payload['data'] != payload['data']:
        raise ValidationError('工具呈现与请求不匹配。')
    _, payload['snapshot_id'] = _response_reference(parsed.attempt, payload['answer_event_id'])
    return payload


def prepare_confidence_observation(parsed):
    from .models import ConfidenceAssignment
    if not uses_capture_v2(parsed.session):
        raise ValidationError('采集契约不匹配。')
    payload = dict(parsed.payload)
    if not ConfidenceAssignment.objects.filter(pk=payload['confidence_assignment_id'], attempt=parsed.attempt,
        condition=ConfidenceAssignment.Condition.PROMPT).exists():
        raise ValidationError('信心记录与实际分配不匹配。')
    _, payload['snapshot_id'] = _response_reference(parsed.attempt, payload['answer_event_id'])
    return payload


def prepare_tool_request(parsed):
    payload = dict(parsed.payload)
    if not uses_capture_v2(parsed.session) or payload['content_version'] != content_version(parsed.attempt):
        raise ValidationError('工具采集版本不匹配。')
    _assert_tool(parsed.attempt, payload['tool'], payload['data'])
    _, payload['snapshot_id'] = _response_reference(parsed.attempt, payload['answer_event_id'])
    return payload
