"""Explicit governance path for the approved KT01 test experiment only."""
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.course_access import require_course_owner
from apps.accounts.models import Course
from apps.analytics.models import ExportJob, ExportSnapshot
from apps.analytics.services import remove_export_artifact
from apps.audit.models import AuditLog
from .models import Experiment


# Every source set is independently bounded by its owning experiment. Shared
# identities, source questions, KC, users and capture/schema artifacts stay out.
OWNED_SCOPES = {
    'experiments.Experiment': 'pk',
    'experiments.ExperimentQuestion': 'experiment_id',
    'experiments.ExperimentQuestionQuota': 'experiment_id',
    'experiments.ExperimentSamplingKC': 'experiment_id',
    'experiments.ExperimentStudent': 'experiment_id',
    'experiments.ExperimentParticipantClass': 'experiment_id',
    'experiments.ExperimentParticipantOverride': 'experiment_id',
    'experiments.ExperimentVersion': 'experiment_id',
    'experiments.ClassExperimentTask': 'experiment_id',
    'experiments.ScheduleRevision': 'class_task__experiment_id',
    'experiments.ExperimentClassSnapshot': 'version__experiment_id',
    'experiments.ExperimentVersionParticipantSnapshot': 'version__experiment_id',
    'experiments.Condition': 'experiment_id',
    'experiments.ConditionAssignment': 'condition__experiment_id',
    'experiments.QuestionSnapshot': 'version__experiment_id',
    'experiments.ItemInstance': 'question_snapshot__version__experiment_id',
    'experiments.ExperimentClassRandomizationIdentity': 'experiment_id',
    'experiments.PublishPlan': 'experiment_id',
    'experiments.PublishPlanClass': 'publish_plan__experiment_id',
    'experiments.PublishPlanExclusion': 'publish_plan_class__publish_plan__experiment_id',
    'experiments.PublishPreviewRun': 'publish_plan__experiment_id',
    'experiments.PublishPreviewClassResult': 'preview_run__publish_plan__experiment_id',
    'experiments.PublishPreviewParticipant': 'class_result__preview_run__publish_plan__experiment_id',
    'experiments.ReleaseSnapshotQuestionMapping': 'release_snapshot__experiment_id',
    'experiments.PublishPreviewItem': 'participant__class_result__preview_run__publish_plan__experiment_id',
    'experiments.PublishPreviewInvalidation': 'preview_run__publish_plan__experiment_id',
    'assessment.ExperimentSession': 'version__experiment_id',
    'assessment.StudentQuestionAssignment': 'session__version__experiment_id',
    'assessment.Attempt': 'session__version__experiment_id',
    'assessment.QuestionVisitEvent': 'session__version__experiment_id',
    'assessment.DraftSaveEvent': 'attempt__session__version__experiment_id',
    'assessment.ResponseInputObservation': 'attempt__session__version__experiment_id',
    'assessment.SubmissionEvent': 'attempt__session__version__experiment_id',
    'assessment.GradeRecord': 'submission__attempt__session__version__experiment_id',
    'assessment.ClientTimeSync': 'session__version__experiment_id',
    'assessment.ClientCaptureCheckpoint': 'session__version__experiment_id',
    'assessment.ResponseSnapshot': 'attempt__session__version__experiment_id',
    'assessment.SimulationSnapshot': 'attempt__session__version__experiment_id',
    'assessment.ConfidenceAssignment': 'attempt__session__version__experiment_id',
    'assessment.AssessmentEvent': 'session__version__experiment_id',
    'analytics.RecordingCoverageSession': 'session__version__experiment_id',
    'analytics.RecordingCoverageAttempt': 'attempt__session__version__experiment_id',
    'analytics.ResearchSessionMaterialization': 'session__version__experiment_id',
    'analytics.ResearchSessionResponse': 'materialization__session__version__experiment_id',
    'analytics.ResearchSessionVisit': 'materialization__session__version__experiment_id',
    'analytics.ResearchSessionBehavior': 'materialization__session__version__experiment_id',
}


def _assert_no_external_references(selections, snapshot_ids, job_ids):
    allowed = {**selections, ExportSnapshot: snapshot_ids, ExportJob: job_ids}
    for model, ids in selections.items():
        if not ids:
            continue
        for relation in model._meta.related_objects:
            referring_model = relation.related_model
            external = referring_model._base_manager.filter(**{f'{relation.field.name}__in': ids})
            external = external.exclude(pk__in=allowed.get(referring_model, []))
            if external.exists():
                raise ValidationError('检测到清理范围外的关联记录；请先核查依赖，未执行清理。')


def _remove_artifacts(job_ids):
    for job in ExportJob.objects.filter(pk__in=job_ids):
        remove_export_artifact(job, reason='test_experiment_purge', invalidated=True)


@transaction.atomic
def purge_test_experiment(*, experiment_id, actor, apply=False):
    if connection.vendor != 'postgresql':
        raise ValidationError('该清理工具必须在 PostgreSQL 上运行。')
    course_id = Experiment.objects.values_list('course_id', flat=True).get(pk=experiment_id)
    course = Course.objects.select_for_update().get(pk=course_id)
    if not actor.is_active:
        raise PermissionDenied
    require_course_owner(course, actor)
    experiment = Experiment.objects.select_for_update().get(pk=experiment_id, course=course)
    if (course.code, course.name, experiment.name) != ('KT01', 'EDA技术', 'test'):
        raise ValidationError('仅允许清理 KT01 / EDA技术 中名称精确为 test 的实验。')

    selections = {}
    for label, field in OWNED_SCOPES.items():
        model = apps.get_model(label)
        selections[model] = list(model._base_manager.select_for_update(of=('self',)).filter(
            **{field: experiment.pk},
        ).order_by('pk').values_list('pk', flat=True))

    versions = selections[apps.get_model('experiments.ExperimentVersion')]
    affected_snapshots = Q(experiment_id=experiment.pk)
    for version_id in versions:
        affected_snapshots |= Q(selected_versions__contains=[{'id': version_id}])
    snapshots = ExportSnapshot.objects.select_for_update().filter(affected_snapshots)
    snapshot_ids = list(snapshots.values_list('pk', flat=True))
    jobs = ExportJob.objects.select_for_update().filter(Q(snapshot_id__in=snapshot_ids) | Q(experiment_id=experiment.pk))
    job_ids = list(jobs.values_list('pk', flat=True))
    if jobs.filter(status=ExportJob.Status.RUNNING).exists():
        raise ValidationError('相关导出仍在生成中，请等待结束后再清理。')
    _assert_no_external_references(selections, snapshot_ids, job_ids)
    result = {
        'applied': apply, 'course_code': course.code, 'course_name': course.name,
        'experiment_name': experiment.name, 'experiment_id': experiment.pk,
        'counts': {model._meta.label: len(ids) for model, ids in selections.items()},
        'export_snapshots_invalidated': len(snapshot_ids), 'export_jobs_invalidated': len(job_ids),
    }
    if not apply:
        return result

    now = timezone.now()
    ExportSnapshot.objects.filter(pk__in=snapshot_ids).update(
        invalidated_at=now, invalidated_reason='test_experiment_purge', source_bundle=None,
        redacted_at=now, filter_definition={'scope_redacted': True}, scope_hash='',
    )
    ExportSnapshot.objects.filter(pk__in=snapshot_ids, experiment_id=experiment.pk).update(experiment_id=None)
    ExportJob.objects.filter(pk__in=job_ids).update(status=ExportJob.Status.INVALIDATED, artifact_removal_reason='test_experiment_purge')
    ExportJob.objects.filter(pk__in=job_ids, experiment_id=experiment.pk).update(experiment_id=None)
    # PostgreSQL checks the complete bounded tree, including cyclic final-head
    # references, before commit. Normal immutable deletion guards stay intact.
    with connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL DEFERRED')
        for model, ids in reversed(list(selections.items())):
            if ids:
                model._base_manager.filter(pk__in=ids)._raw_delete(using=connection.alias)
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        cursor.execute('SET CONSTRAINTS ALL DEFERRED')
    AuditLog.objects.create(actor=actor, action='experiment.test_purged', object_type='Experiment',
                            object_id=str(experiment_id), details=result)
    transaction.on_commit(lambda: _remove_artifacts(job_ids))
    return result
