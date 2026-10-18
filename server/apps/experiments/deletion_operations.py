"""Register confirmation-bound intent; execute once and recover unknown outcomes."""
import hashlib
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.accounts.course_access import require_course_manager, require_writable_course
from .models import ResearchDeletionOperation
from .research_deletion import DeletionConflict, TOKEN_SALT, _deletion_selection, deletion_preview, execute_deletion


def operation_payload(operation):
    return {'operation_id': str(operation.pk), 'state': operation.state,
            'execute_before': operation.execute_before, 'result': operation.result or None,
            'error_code': operation.error_code, 'message': operation.public_message}


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _validate_identity(operation, *, actor, action, object_id, class_id, token):
    if (operation.actor_id != actor.pk or operation.action != action or operation.object_id != object_id
            or operation.class_id != class_id or operation.confirmation_token_hash != _token_hash(token)):
        raise DeletionConflict('删除操作编号与原确认范围不一致。', code='DELETION_IDEMPOTENCY_CONFLICT')


def _context(course, *, action, object_id, class_id):
    from django.shortcuts import get_object_or_404
    if action == 'delete_class':
        return dict(course=course, action=action, experiment=None,
                    class_group=get_object_or_404(course.classes, pk=object_id))
    return dict(course=course, action=action,
                experiment=get_object_or_404(course.experiments, pk=object_id),
                class_group=get_object_or_404(course.classes, pk=class_id) if action == 'remove_class_experiment' else None)


def register_deletion_operation(*, course, actor, operation_id, action, object_id, class_id, confirmation_token):
    require_course_manager(course, actor)
    require_writable_course(course)
    if action == 'delete_class':
        class_id = object_id
    existing = ResearchDeletionOperation.objects.filter(pk=operation_id, course=course).first()
    if existing:
        _validate_identity(existing, actor=actor, action=action, object_id=object_id, class_id=class_id, token=confirmation_token)
        return existing, False
    try:
        previous = signing.loads(confirmation_token, salt=TOKEN_SALT, max_age=1800)
    except signing.BadSignature as error:
        raise DeletionConflict('删除确认已失效，请重新预览并确认。') from error
    context = _context(course, action=action, object_id=object_id, class_id=class_id)
    selection = _deletion_selection(**context)
    current = deletion_preview(actor=actor, _selection=selection, **context)
    if not current['allowed'] or previous != signing.loads(current['confirmation_token'], salt=TOKEN_SALT):
        raise DeletionConflict('删除影响范围已变化，请重新预览并确认。')
    now = timezone.now()
    operation, created = ResearchDeletionOperation.objects.get_or_create(operation_id=operation_id, defaults={
        'actor': actor, 'course': course, 'action': action, 'object_id': object_id, 'class_id': class_id,
        'scope_fingerprint': previous['fingerprint'], 'frozen_scope': {**previous,
            'session_ids': selection['session_ids'], 'task_ids': selection['task_ids'],
            'participant_ids': selection['participant_ids'],
            'coverage_run_ids': list(map(str, selection['coverage_run_ids'])),
            'export_snapshot_ids': list(map(str, selection['snapshots'].values_list('pk', flat=True))),
            'counts': current['counts']},
        'confirmation_token_hash': _token_hash(confirmation_token), 'accepted_at': now,
        'execute_before': now + timedelta(seconds=30),
    })
    if operation.course_id != course.pk:
        raise DeletionConflict('删除操作编号与课程不一致。', code='DELETION_IDEMPOTENCY_CONFLICT')
    _validate_identity(operation, actor=actor, action=action, object_id=object_id, class_id=class_id, token=confirmation_token)
    return operation, created


def _reject(operation, code, message):
    operation.state = 'rejected'
    operation.error_code, operation.public_message = code, message
    operation.finished_at = timezone.now()
    operation.save(update_fields=['state', 'error_code', 'public_message', 'finished_at'])


def execute_registered_deletion(*, request, course, operation_id, action, object_id, class_id, confirmation_token):
    require_course_manager(course, request.user)
    if action == 'delete_class':
        class_id = object_id
    failure = None
    result = None
    from .research_deletion import _deletion_transaction
    with _deletion_transaction():
        from django.shortcuts import get_object_or_404
        operation = get_object_or_404(ResearchDeletionOperation.objects.select_for_update(nowait=True),
                                      pk=operation_id, course=course, actor=request.user)
        _validate_identity(operation, actor=request.user, action=action, object_id=object_id,
                           class_id=class_id, token=confirmation_token)
        if operation.state == 'succeeded':
            return operation.result
        if operation.state == 'rejected':
            failure = DeletionConflict(operation.public_message, code=operation.error_code)
        elif operation.execute_before <= timezone.now():
            _reject(operation, 'DELETION_EXECUTION_EXPIRED', '删除操作未在开始期限内执行，请重新预览确认。')
            failure = DeletionConflict(operation.public_message, code=operation.error_code)
        else:
            try:
                with transaction.atomic():
                    result = execute_deletion(request=request, confirmation_token=confirmation_token,
                        **_context(course, action=action, object_id=object_id, class_id=class_id))
            except DeletionConflict as error:
                _reject(operation, error.code, '；'.join(error.messages))
                failure = error
            else:
                operation.state = 'succeeded'
                operation.finished_at = timezone.now()
                operation.result = {'deleted': result['deleted'], 'cleanup_pending': result['cleanup_pending'],
                    'cleanup_id': result['cleanup_id'], 'operation_id': str(operation.pk)}
                operation.save(update_fields=['state', 'finished_at', 'result'])
                result = operation.result
    if failure:
        raise failure
    return result


def recover_pending_deletion_operations(*, now=None, limit=100):
    now = now or timezone.now()
    with transaction.atomic():
        operations = list(ResearchDeletionOperation.objects.select_for_update(skip_locked=True)
            .filter(state='pending', execute_before__lte=now).order_by('execute_before')[:limit])
        for operation in operations:
            _reject(operation, 'DELETION_EXECUTION_EXPIRED', '删除未执行或事务已回滚，请重新预览确认。')
    return len(operations)
