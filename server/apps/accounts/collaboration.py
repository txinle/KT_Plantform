from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from .models import Course, CourseTeacher, User, default_course_permissions
from .course_access import require_course_owner, require_writable_course

PERMISSIONS = [('bank', '题库与知识点'), ('class', '班级与学生'), ('experiment', '实验管理'), ('data', '数据查看'), ('export', '数据导出')]


def collaboration_token(course):
    return signing.dumps({'id': course.pk, 'owner': course.owner_id, 'updated': course.updated_at.isoformat(), 'members': list(course.teacher_links.order_by('teacher_id').values_list('teacher_id', 'permissions'))}, salt='course-collaboration', compress=True)


def check_token(course, token):
    try:
        if signing.loads(token, salt='course-collaboration') != signing.loads(collaboration_token(course), salt='course-collaboration'):
            raise ValidationError('课程协作设置已变更，请刷新后重试。')
    except (signing.BadSignature, TypeError):
        raise ValidationError('课程协作设置已变更，请刷新后重试。')


def person(user):
    return {'id': user.pk, 'display_name': user.display_name or user.username, 'username': user.username, 'is_active': user.is_active}


def collaboration_payload(course, actor):
    full = actor.is_superuser or actor.role == User.Role.ADMIN or course.owner_id == actor.pk
    link = course.teacher_links.filter(teacher=actor).first()
    writable = course.status == Course.Status.ACTIVE
    return {'redirect_url': None if full or link else '/teacher/courses/', 'course': {'id': course.pk, 'name': course.name, 'code': course.code, 'term': course.term, 'status': course.status}, 'owner': person(course.owner), 'members': [{**person(link.teacher), 'permissions': link.permissions} for link in course.teacher_links.select_related('teacher').order_by('teacher_id')], 'permission_options': [{'key': key, 'label': label} for key, label in PERMISSIONS], 'granted_permissions': default_course_permissions() if full else (link.permissions if link else []), 'capabilities': {'can_manage_members': full and writable, 'can_transfer': full and writable, 'can_update': full and writable, 'can_archive': full and writable}, 'state_token': collaboration_token(course)}


@transaction.atomic
def mutate_collaboration(course, actor, *, action, teacher_id, permissions=None, state_token, request=None, keep_previous=True, reason=""):
    course = Course.objects.select_for_update().get(pk=course.pk)
    require_course_owner(course, actor)
    require_writable_course(course)
    check_token(course, state_token)
    teacher = get_object_or_404(User.objects.select_for_update(), pk=teacher_id, role=User.Role.TEACHER)
    if action != 'remove' and not teacher.is_active:
        raise ValidationError('请选择启用的教师账号。')
    if teacher.pk == course.owner_id:
        raise ValidationError('课程负责人无需重复添加为协作教师。')
    before = list(course.teacher_links.values('teacher_id', 'permissions'))
    if action in ('add', 'update'):
        if not isinstance(permissions, list) or not permissions or any(p not in default_course_permissions() for p in permissions):
            raise ValidationError('至少选择一项有效权限。')
        if 'export' in permissions and 'data' not in permissions:
            raise ValidationError('数据导出需要同时授予数据查看权限。')
        if action == 'add':
            if course.teacher_links.filter(teacher=teacher).exists():
                raise ValidationError('该教师已经是协作成员。')
            CourseTeacher.objects.create(course=course, teacher=teacher, permissions=sorted(set(permissions)))
        else:
            link = get_object_or_404(course.teacher_links, teacher=teacher)
            link.permissions = sorted(set(permissions))
            link.save(update_fields=['permissions'])
    elif action == 'remove':
        get_object_or_404(course.teacher_links, teacher=teacher).delete()
    elif action == 'transfer':
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError('请填写移交原因。')
        old_owner = course.owner
        course.owner = teacher
        course.full_clean()
        course.teacher_links.filter(teacher=teacher).delete()
        if keep_previous and old_owner.is_active:
            CourseTeacher.objects.update_or_create(course=course, teacher=old_owner, defaults={'permissions': ['bank', 'class', 'experiment', 'data']})
    else:
        raise ValidationError('未知协作操作。')
    course.save(update_fields=['owner', 'updated_at'])
    if request:
        from apps.audit.services import log_action
        log_action(request, 'course.collaboration.' + action, course, {'course_id': course.pk, 'teacher_id': teacher.pk, 'reason': reason.strip(), 'before': before, 'after': list(course.teacher_links.values('teacher_id', 'permissions'))})
    return course


def transfer_course_owner(course, actor, teacher, state_token, request=None, keep_previous=True, reason=""):
    return mutate_collaboration(course, actor, action='transfer', teacher_id=teacher.pk, state_token=state_token, request=request, keep_previous=keep_previous, reason=reason)
