"""Reversible administrator removal; original domain rows retain their identities."""
import hashlib
from datetime import timedelta

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import log_action
from apps.experiments.research_deletion import DeletionConflict, _deletion_transaction
from .models import AdminRecycleEntry, Course, User

SALT = 'admin-recycle-v1'


def _authorize(actor):
    if not actor.is_active or not (actor.is_superuser or actor.role == User.Role.ADMIN):
        raise PermissionDenied


def _user_guard(user, actor):
    if user.pk == actor.pk:
        raise ValidationError('不能删除当前登录账号。')
    if (user.is_superuser or user.role == User.Role.ADMIN) and not User.objects.filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True), is_active=True,
            deleted_at__isnull=True).exclude(pk=user.pk).exists():
        raise ValidationError('不能删除最后一个可用管理员。')


def _scope(kind, obj, actor):
    if kind == 'course':
        from .course_deletion import _selection
        counts = {key: qs.count() for key, qs in _selection(obj).items() if key != 'courses'}
        state = {'status': obj.status, 'updated_at': obj.updated_at.isoformat()}
        name = obj.name
    else:
        _user_guard(obj, actor)
        counts = {'owned_courses': obj.owned_courses.count(), 'enrollments': obj.course_enrollments.count()}
        state = {'is_active': obj.is_active, 'username': obj.username, 'name': obj.display_name,
                 'role': obj.role, 'is_superuser': obj.is_superuser}
        name = obj.shown_name
    return {'kind': kind, 'object_id': obj.pk, 'name': name, 'state': state, 'counts': counts, 'actor_id': actor.pk}


def preview(kind, object_id, actor):
    _authorize(actor)
    model = Course if kind == 'course' else User
    obj = get_object_or_404(model.objects, pk=object_id, deleted_at__isnull=True)
    scope = _scope(kind, obj, actor)
    return {'name': scope['name'], 'counts': scope['counts'], 'allowed': True, 'blocked_reason': '',
            'retention_days': 14, 'confirmation_token': signing.dumps(scope, salt=SALT, compress=True)}


def entry_row(entry):
    return {'id': entry.pk, 'kind': entry.kind, 'object_id': entry.object_id, 'name': entry.name,
            'deleted_at': entry.deleted_at.isoformat(), 'restore_until': entry.restore_until.isoformat(),
            'can_restore': entry.restored_at is None and timezone.now() < entry.restore_until,
            'counts': entry.counts}


def _result(entry):
    return {'deleted': True, 'recoverable': entry.restored_at is None and timezone.now() < entry.restore_until, 'recycle_id': entry.pk,
            'operation_id': str(entry.pk), 'restore_until': entry.restore_until.isoformat()}


def remove(request, kind, object_id, values):
    _authorize(request.user)
    token = values.get('confirmation_token', '')
    if values.get('confirmation') != 'DELETE' or not values.get('confirmation_name') or not isinstance(token, str):
        raise ValidationError('请预览影响并明确确认删除。')
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # A retried response must never delete an object restored in the meantime.
    previous = AdminRecycleEntry.objects.filter(kind=kind, object_id=object_id,
        actor=request.user, confirmation_hash=token_hash).first()
    if previous:
        return {**_result(previous), 'restored': previous.restored_at is not None}
    try:
        expected = signing.loads(token, salt=SALT, max_age=600)
    except (signing.BadSignature, TypeError) as exc:
        raise DeletionConflict('删除预览已失效，请重新预览。') from exc
    with _deletion_transaction():
        if kind == 'user':
            list(User.objects.select_for_update().filter(Q(role='admin') | Q(is_superuser=True)).order_by('pk'))
        manager = Course.all_objects if kind == 'course' else User.objects
        obj = get_object_or_404(manager.select_for_update(), pk=object_id)
        if obj.deleted_at is not None:
            previous = AdminRecycleEntry.objects.filter(kind=kind, object_id=object_id,
                actor=request.user, confirmation_hash=token_hash).first()
            if previous:
                return _result(previous)
            raise DeletionConflict('对象已在回收站，请刷新列表。')
        scope = _scope(kind, obj, request.user)
        if scope != expected or values.get('confirmation_name') != scope['name']:
            raise DeletionConflict('对象或关联范围已变化，请重新预览确认。')
        now = timezone.now()
        entry = AdminRecycleEntry.objects.create(kind=kind, object_id=obj.pk, name=scope['name'],
            actor=request.user, deleted_at=now, restore_until=now+timedelta(days=14),
            previous_state=scope['state'], counts=scope['counts'], confirmation_hash=token_hash)
        obj.deleted_at = now
        if kind == 'course':
            obj.status = Course.Status.ARCHIVED
            obj.save(update_fields=['deleted_at', 'status', 'updated_at'])
        else:
            obj.is_active = False
            obj.save(update_fields=['deleted_at', 'is_active'])
        log_action(request, f'admin.{kind}.recycle', obj,
                   {'recycle_id': entry.pk, 'counts': entry.counts, 'restore_until': entry.restore_until.isoformat()})
    return _result(entry)


@transaction.atomic
def restore(request, entry_id):
    _authorize(request.user)
    entry = get_object_or_404(AdminRecycleEntry.objects.select_for_update(), pk=entry_id)
    if entry.restored_at:
        return {'restored': True, 'id': entry.pk}
    if timezone.now() >= entry.restore_until:
        raise DeletionConflict('已超过 14 天恢复期限，不能恢复。')
    manager = Course.all_objects if entry.kind == 'course' else User.objects
    obj = get_object_or_404(manager.select_for_update(), pk=entry.object_id)
    if obj.deleted_at != entry.deleted_at:
        raise DeletionConflict('对象删除状态已变化，请刷新回收站。')
    obj.deleted_at = None
    field = 'status' if entry.kind == 'course' else 'is_active'
    setattr(obj, field, entry.previous_state[field])
    obj.save(update_fields=['deleted_at', field] + (['updated_at'] if entry.kind == 'course' else []))
    entry.restored_at = timezone.now()
    entry.save(update_fields=['restored_at'])
    log_action(request, f'admin.{entry.kind}.restore', obj, {'recycle_id': entry.pk})
    return {'restored': True, 'id': entry.pk}
