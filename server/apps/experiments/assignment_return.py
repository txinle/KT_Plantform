"""Teacher-confirmed reset of one participant, retaining the frozen assignment."""
import hashlib

from django.core import signing
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.accounts.course_access import accessible_courses, lock_writable_course, require_writable_course, require_course_manager
from apps.analytics.models import ResearchSessionMaterialization, StudentExperimentResultSnapshot
from apps.api.exceptions import ApiConflict
from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.models import ExperimentSession, SessionSubmission
from apps.assessment.prepared_services import prepare_participant
from apps.audit.models import AuditLog
from .models import ClassExperimentTask, Experiment, ExperimentVersionParticipantSnapshot
from .research_deletion import _coverage_runs, _deletion_transaction, _purge_sessions


SALT = 'student-assignment-return-v1'
ACTION = 'experiment.student_session_reset'


def _task(task_id, actor):
    return get_object_or_404(ClassExperimentTask.objects.select_related('experiment__course', 'class_group'),
        pk=task_id, experiment__course__in=accessible_courses(actor, capability='experiment'))


def _state(task, session):
    return {
        'session': session.pk if session else None,
        'status': session.status if session else None,
        'research_revision': session.research_source_revision if session else None,
        'result_revision': session.result_revision if session else None,
        'ingest_sequence': session.next_ingest_sequence_no if session else None,
        'submissions': session.submission_count if session else None,
        'activity': session.last_activity_at.isoformat() if session else None,
        'schedule': task.schedule_revisions.order_by('-revision_no').values_list('revision_no', flat=True).first(),
    }


def _blocked(task, session):
    if not DeadlineDecisionService.can_start_session(task, timezone.now()).allowed:
        return '该班级作业当前未开放，请先调整排期后再打回。'
    if not session or session.status == ExperimentSession.Status.PREPARED:
        return '该学生尚未开始，无需打回。'
    if session.status == ExperimentSession.Status.SUBMITTING or SessionSubmission.objects.filter(session=session, status='running').exists():
        return '该学生正在交卷，请等待处理完成后重试。'
    if ResearchSessionMaterialization.objects.filter(session=session, status='running').exists():
        return '该学生的作答数据正在处理，请稍后重试。'
    if _coverage_runs([session.pk]):
        return '该作答已关联研究采集校验结果，暂不能打回。'
    return ''


def preview_assignment_return(*, task_id, participant_id, actor):
    task = _task(task_id, actor)
    require_writable_course(task.experiment.course)
    participant = get_object_or_404(task.participant_snapshots.select_related('student'), pk=participant_id)
    session = ExperimentSession.objects.filter(class_task=task, participant_snapshot=participant).first()
    reason = _blocked(task, session)
    token = None if reason else signing.dumps({'actor': actor.pk, 'task': task.pk,
        'participant': participant.pk, 'state': _state(task, session)}, salt=SALT, compress=True)
    return {'participant_id': participant.pk, 'student_number': participant.student.student_number or participant.student.username,
        'display_name': participant.student.shown_name, 'class_name': str(task.class_group),
        'question_count': len(participant.assignment_snapshot),
        'submission_count': session.submission_count if session else 0,
        'can_return': not reason, 'blocked_reason': reason, 'confirmation_token': token}


def return_assignment(*, task_id, participant_id, actor, confirmation_token):
    task = _task(task_id, actor)
    try:
        confirmed = signing.loads(confirmation_token, salt=SALT, max_age=600)
    except signing.BadSignature as exc:
        raise ApiConflict('确认信息已失效，请重新选择学生并核对。', code='RETURN_CONFIRMATION_EXPIRED') from exc
    if any(confirmed.get(key) != value for key, value in (
        ('actor', actor.pk), ('task', task_id), ('participant', participant_id))):
        raise ApiConflict('确认信息与当前学生不匹配，请重新核对。', code='RETURN_SCOPE_CHANGED')
    request_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
    with _deletion_transaction():
        course = lock_writable_course(task.experiment.course_id)
        require_course_manager(course, actor, capability='experiment')
        Experiment.objects.select_for_update().get(pk=task.experiment_id)
        task = ClassExperimentTask.objects.select_for_update().get(pk=task_id)
        participant = get_object_or_404(ExperimentVersionParticipantSnapshot.objects.select_for_update(),
            pk=participant_id, class_task=task)
        completed = AuditLog.objects.filter(actor=actor, action=ACTION, details__request_hash=request_hash).first()
        if completed:
            return completed.details['result']
        session = ExperimentSession.objects.select_for_update().filter(class_task=task, participant_snapshot=participant).first()
        reason = _blocked(task, session)
        if reason:
            raise ApiConflict(reason, code='RETURN_NOT_AVAILABLE')
        if confirmed['state'] != _state(task, session):
            raise ApiConflict('该学生的作答或排期已变化，请重新选择学生并核对。', code='RETURN_SCOPE_CHANGED')
        old_id = session.pk
        old_order = list(session.assignments.order_by('assigned_sequence_index').values_list('snapshot_id', flat=True))
        counts = {}
        _purge_sessions([old_id], counts, [])
        StudentExperimentResultSnapshot.objects.filter(class_task=task, student_id=participant.student_id).delete()
        artifact = prepare_participant(participant)
        new = artifact.session
        if not artifact.ready or list(new.assignments.order_by('assigned_sequence_index').values_list('snapshot_id', flat=True)) != old_order:
            raise ApiConflict('原试卷重建失败，作答记录未清除。', code='RETURN_REBUILD_FAILED')
        ClassExperimentTask.objects.filter(pk=task.pk).bump_result_revision()
        result = {'participant_id': participant.pk, 'session_id': new.pk, 'status': 'not_started'}
        AuditLog.objects.create(actor=actor, action=ACTION, object_type='Experiment', object_id=str(task.experiment_id),
            details={'student_id': participant.student_id, 'class_task_id': task.pk, 'old_session_id': old_id,
                'new_session_id': new.pk, 'removed_counts': counts, 'backup_created': False,
                'request_hash': request_hash, 'result': result})
        return result
