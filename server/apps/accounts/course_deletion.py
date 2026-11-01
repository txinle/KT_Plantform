"""Confirmed whole-course removal; files are retired by the existing durable queue."""
import hashlib
import json
from contextlib import ExitStack
from datetime import timedelta
from uuid import uuid4

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts import models as accounts
from apps.analytics import models as analytics
from apps.analytics.research_deletion import affected_export_snapshots, cleanup_state, retire_research_exports
from apps.assessment import models as assessment
from apps.audit.services import log_action
from apps.experiments import models as experiments
from apps.experiments.publication_services import class_task_business_status
from apps.experiments.research_deletion import (
    DeletionConflict, _coverage_runs, _deletion_transaction, _purge_experiment_relations,
    _purge_sessions, _raw_remove,
)
from apps.question_bank import models as banks

SALT = 'confirmed-whole-course-deletion-v1'


def recover_course_deletion(course_id, actor, confirmation_token):
    """Resolve a lost successful response using the original actor-bound confirmation."""
    _authorize(actor)
    if not isinstance(confirmation_token, str):
        return None
    operation = experiments.ResearchDeletionOperation.objects.filter(
        actor=actor, action='delete_course', object_id=course_id, state='succeeded',
        confirmation_token_hash=hashlib.sha256(confirmation_token.encode()).hexdigest(),
    ).first()
    if operation is None:
        return None
    return {**operation.result, **cleanup_state(operation.result.get('cleanup_id'))}


def _authorize(actor):
    if not actor.is_active or not (actor.is_superuser or actor.role == accounts.User.Role.ADMIN):
        raise PermissionDenied('只有平台管理员可以删除整门课程。')


def _selection(course):
    exps = experiments.Experiment.objects.filter(course=course)
    sessions = assessment.ExperimentSession.objects.filter(version__experiment__in=exps)
    coverage = _coverage_runs(list(sessions.values_list('pk', flat=True)))
    snapshots = affected_export_snapshots(course_id=course.pk, experiment_ids=list(exps.values_list('pk', flat=True)), coverage_run_ids=coverage)
    snapshots = (analytics.ExportSnapshot.objects.filter(Q(pk__in=snapshots) | Q(course=course))
                 if exps.exists() else analytics.ExportSnapshot.objects.filter(course=course))
    return {
        'courses': accounts.Course.objects.filter(pk=course.pk),
        'classes': accounts.ClassGroup.objects.filter(course=course),
        'members': accounts.CourseTeacher.objects.filter(course=course),
        'enrollments': accounts.CourseEnrollment.objects.filter(course=course),
        'access_periods': accounts.CourseEnrollmentAccessPeriod.objects.filter(course=course),
        'subjects': accounts.CourseSubject.objects.filter(course=course),
        'study_links': accounts.ResearchStudyCourse.objects.filter(course=course),
        'imports': accounts.StudentImportJob.objects.filter(course=course),
        'banks': banks.QuestionBank.objects.filter(course=course),
        'questions': banks.Question.objects.filter(bank__course=course),
        'question_links': banks.QuestionConcept.objects.filter(question__bank__course=course),
        'question_identities': banks.CourseQuestionIdentity.objects.filter(course=course),
        'import_previews': banks.ImportPreviewBatch.objects.filter(course=course),
        'concepts': banks.Concept.objects.filter(course=course),
        'experiments': exps,
        'plans': experiments.PublishPlan.objects.filter(experiment__in=exps),
        'participants': experiments.ExperimentVersionParticipantSnapshot.objects.filter(version__experiment__in=exps),
        'tasks': experiments.ClassExperimentTask.objects.filter(experiment__in=exps),
        'schedules': experiments.ScheduleRevision.objects.filter(class_task__experiment__in=exps),
        'sessions': sessions,
        'attempts': assessment.Attempt.objects.filter(session__in=sessions),
        'events': assessment.AssessmentEvent.objects.filter(session__in=sessions),
        'submissions': assessment.SessionSubmission.objects.filter(session__in=sessions),
        'exports': analytics.ExportJob.objects.filter(snapshot__in=snapshots),
        'snapshots': snapshots,
        'assets': analytics.ResearchExportAsset.objects.filter(course=course),
        'coverage': analytics.RecordingCoverageRun.objects.filter(pk__in=coverage),
        'cleanup_receipts': accounts.DataPurgeRequest.objects.filter(scope_definition__course_id=course.pk),
    }


def _blocked_reason(course, selection):
    if any(class_task_business_status(task) in ('scheduled', 'open') for task in selection['tasks']):
        return '存在进行中或尚未开始的班级任务，请先处理任务安排。'
    if selection['imports'].filter(status__in=['queued', 'running']).exists():
        return '学生导入尚未完成，请等待完成后重新预览。'
    if assessment.SessionSubmission.objects.filter(session__in=selection['sessions'], status='running').exists() or analytics.ResearchSessionMaterialization.objects.filter(session__in=selection['sessions'], status='running').exists():
        return '交卷或研究整理任务正在执行，请稍后重试。'
    external = [
        experiments.Experiment.objects.filter(question_bank__course=course).exclude(course=course),
        experiments.ExperimentQuestionBank.objects.filter(question_bank__course=course).exclude(experiment__course=course),
        experiments.ExperimentQuestion.objects.filter(question__bank__course=course).exclude(experiment__course=course),
        experiments.QuestionSnapshot.objects.filter(source_question__bank__course=course).exclude(version__experiment__course=course),
        experiments.ReleaseSnapshotQuestionMapping.objects.filter(source_question__bank__course=course).exclude(release_snapshot__experiment__course=course),
        experiments.PublishPreviewItem.objects.filter(source_question__bank__course=course).exclude(participant__class_result__preview_run__publish_plan__experiment__course=course),
        banks.QuestionConcept.objects.filter(concept__course=course).exclude(question__bank__course=course),
        banks.Concept.objects.filter(parent__course=course).exclude(course=course),
        experiments.ExperimentSamplingKC.objects.filter(concept__course=course).exclude(experiment__course=course),
        accounts.CourseEnrollment.objects.filter(class_group__course=course).exclude(course=course),
        experiments.ClassExperimentTask.objects.filter(class_group__course=course).exclude(experiment__course=course),
        experiments.ExperimentParticipantClass.objects.filter(class_group__course=course).exclude(experiment__course=course),
        experiments.ExperimentClassSnapshot.objects.filter(source_class_group__course=course).exclude(version__experiment__course=course),
        experiments.ExperimentVersionParticipantSnapshot.objects.filter(course_subject__course=course).exclude(version__experiment__course=course),
    ]
    if any(qs.exists() for qs in external):
        return '课程资源仍被其他课程或独立实验引用，不能删除；请先处理跨课程引用。'
    return ''


def preview_course_deletion(course, actor):
    _authorize(actor)
    selection = _selection(course)
    # Include row contents, not just counts: equal-sized replacements invalidate confirmation.
    digest = hashlib.sha256()
    for key, qs in selection.items():
        digest.update(key.encode())
        for row in qs.order_by('pk').values().iterator(chunk_size=500):
            digest.update(json.dumps(row, sort_keys=True, default=str).encode())
    counts = {key: qs.count() for key, qs in selection.items() if key != 'courses'}
    reason = _blocked_reason(course, selection)
    identity = {'course_id': course.pk, 'course_name': course.name, 'actor_id': actor.pk, 'fingerprint': digest.hexdigest()}
    return {'course_id': course.pk, 'name': course.name, 'counts': counts,
            'allowed': not reason, 'blocked_reason': reason,
            'confirmation_token': signing.dumps(identity, salt=SALT, compress=True)}


def execute_course_deletion(request, course, confirmation_token, confirmation_name):
    _authorize(request.user)
    recovered = recover_course_deletion(course.pk, request.user, confirmation_token)
    if recovered:
        return recovered
    try:
        expected = signing.loads(confirmation_token, salt=SALT, max_age=600)
    except (signing.BadSignature, TypeError) as exc:
        raise DeletionConflict('删除预览已失效，请重新预览。') from exc
    initial = _selection(course)
    from apps.analytics.services import _export_file_lock
    with ExitStack() as locks:
        for job_id in initial['exports'].order_by('pk').values_list('pk', flat=True):
            try:
                locks.enter_context(_export_file_lock(job_id, blocking=False))
            except BlockingIOError as exc:
                raise DeletionConflict('导出文件正在生成，请稍后重试。') from exc
        with _deletion_transaction():
            course = accounts.Course.objects.select_for_update(nowait=True).get(pk=course.pk)
            selection = _selection(course)
            for qs in selection.values():
                list(qs.select_for_update(nowait=True).order_by('pk').values_list('pk', flat=True))
            preview = preview_course_deletion(course, request.user)
            if expected != signing.loads(preview['confirmation_token'], salt=SALT) or confirmation_name != course.name:
                raise DeletionConflict('课程名称或删除范围已变化，请重新预览确认。')
            if not preview['allowed']:
                raise DeletionConflict(preview['blocked_reason'])
            course_id = course.pk
            operation = experiments.ResearchDeletionOperation.objects.create(
                operation_id=uuid4(), actor=request.user, course=course, action='delete_course', object_id=course_id,
                scope_fingerprint=expected['fingerprint'], frozen_scope={**expected, 'counts': preview['counts']},
                confirmation_token_hash=hashlib.sha256(confirmation_token.encode()).hexdigest(),
                execute_before=timezone.now() + timedelta(seconds=30))
            session_ids = list(selection['sessions'].values_list('pk', flat=True))
            coverage_ids = list(selection['coverage'].values_list('pk', flat=True))
            blobs = list(assessment.SimulationSnapshot.objects.filter(attempt__session_id__in=session_ids).values_list('blob_storage_key', flat=True))
            files = [{'kind': 'storage', 'path': value} for value in blobs if value]
            files += [{'kind': 'research_asset', 'path': value} for value in selection['assets'].values_list('artifact_path', flat=True) if value]
            retired = retire_research_exports(course_id=course_id, experiment_ids=list(selection['experiments'].values_list('pk', flat=True)), coverage_run_ids=coverage_ids, reason='delete_course', actor=request.user, extra_files=files, whole_course=True)
            counts = {}
            _raw_remove(selection['assets'], counts)
            _purge_sessions(session_ids, counts, coverage_ids)
            for experiment in list(selection['experiments'].order_by('pk')):
                _purge_experiment_relations(experiment, counts)
            banks.ImportPreviewBatch.objects.filter(course=course).delete()
            selection['banks'].delete()
            banks.Concept.objects.filter(course=course).update(parent=None)
            selection['concepts'].delete()
            banks.CourseQuestionIdentity.objects.filter(course=course).delete()
            for key in ['imports', 'access_periods', 'enrollments', 'subjects', 'study_links']:
                selection[key].delete()
            accounts.User.objects.filter(class_group__course=course).update(class_group=None)
            accounts.ClassStudentAnalyticsPolicy.objects.filter(class_group__course=course).delete()
            selection['classes'].delete()
            # Receipts retain the original course identity in frozen_scope.
            for receipt in experiments.ResearchDeletionOperation.objects.filter(course=course):
                receipt.frozen_scope = {**receipt.frozen_scope, 'course_id': course_id, 'course_name': course.name}
                receipt.course = None
                receipt.save(update_fields=['course', 'frozen_scope'])
            try:
                course.delete()
            except ProtectedError as exc:
                raise DeletionConflict('仍有未涵盖的历史引用，未删除任何课程数据。') from exc
            result = {'deleted': True, 'course_id': course_id, 'operation_id': str(operation.pk), **cleanup_state(retired['cleanup_id'])}
            operation.course = None
            operation.state = 'succeeded'
            operation.result = json.loads(json.dumps(result, default=str))
            operation.finished_at = timezone.now()
            operation.save(update_fields=['course', 'state', 'result', 'finished_at'])
            log_action(request, 'admin.course.delete', details={**expected, 'counts': counts, 'operation_id': str(operation.pk), 'cleanup_id': retired['cleanup_id']})
    return result
