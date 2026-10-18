"""Explicit, confirmed deletion of an experiment or one class's participation."""

import hashlib
import json
from contextlib import ExitStack, contextmanager

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.accounts.course_access import require_writable_course, require_course_manager
from apps.accounts.models import ClassGroup, CourseEnrollment, User
from apps.analytics.models import (
    ExportJob,
    ExportSnapshot,
    RecordingCoverageAttempt,
    RecordingCoverageRun,
    RecordingCoverageSession,
    ResearchSessionBehavior,
    ResearchSessionQuality,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
    ResearchSessionVisit,
    StudentClassTaskComparison,
    StudentExperimentResultSnapshot,
    StudentTaskComparisonMember,
)
from apps.analytics.research_deletion import affected_export_snapshots, cleanup_state, retire_research_exports
from apps.assessment import models as assessment
from apps.audit.services import log_action

from . import models as experiments


CLASS_BLOCKED = "该班级仍有关联实验及实验数据，请先删除相关实验数据后再删除班级。"
TOKEN_SALT = "research-domain-deletion-v1"


class DeletionConflict(ValidationError):
    def __init__(self, message, *, code='DELETION_SCOPE_CHANGED'):
        super().__init__(message, code=code)


@contextmanager
def _deletion_transaction():
    try:
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '1000ms'")
            yield
    except OperationalError as error:
        sqlstate = getattr(error.__cause__, 'sqlstate', None)
        if sqlstate == '55P03':
            raise DeletionConflict('相关数据正在处理，请稍后重新核对并删除。', code='DELETION_BUSY') from error
        raise


def class_sessions(class_id):
    return assessment.ExperimentSession.objects.filter(
        Q(class_task__class_group_id=class_id) | Q(class_snapshot__source_class_group_id=class_id))


def unattributed_sessions(course_id):
    return assessment.ExperimentSession.objects.filter(
        version__experiment__course_id=course_id,
        class_task__isnull=True,
    ).filter(Q(class_snapshot__isnull=True) | Q(class_snapshot__source_class_group__isnull=True))


def class_experiments(class_group):
    # Published attribution comes from frozen class/session facts, never a student's
    # current membership. Current membership is used only for editable draft rules.
    members = CourseEnrollment.objects.filter(class_group=class_group).values("student_id")
    return experiments.Experiment.objects.filter(course_id=class_group.course_id).filter(
        Q(participant_class_links__class_group=class_group)
        | Q(class_tasks__class_group=class_group)
        | Q(versions__class_snapshots__source_class_group=class_group)
        | Q(class_randomization_identities__class_group=class_group)
        | Q(publish_plans__class_plans__class_group=class_group)
        | Q(pk__in=class_sessions(class_group.pk).values("version__experiment_id"))
        | Q(pk__in=unattributed_sessions(class_group.course_id).values("version__experiment_id"))
        | Q(status=experiments.Experiment.Status.DRAFT, participant_override_links__student_id__in=members)
        | Q(status=experiments.Experiment.Status.DRAFT, student_links__student_id__in=members)
    ).distinct()


def _experiment_classes(experiment):
    return ClassGroup.objects.filter(course_id=experiment.course_id).filter(
        Q(experiment_tasks__experiment=experiment)
        | Q(experiment_class_snapshots__version__experiment=experiment)
        | Q(experiment_participant_class_links__experiment=experiment)
        | Q(experiment_randomization_identities__experiment=experiment)
        | Q(publish_plan_entries__publish_plan__experiment=experiment)
    ).distinct().order_by("pk")


def _experiment_row(experiment, class_id=None):
    sessions = assessment.ExperimentSession.objects.filter(version__experiment=experiment)
    classes = [{"id": item.pk, "name": str(item),
                "session_count": class_sessions(item.pk).filter(version__experiment=experiment).count()}
               for item in _experiment_classes(experiment)]
    snapshots = affected_export_snapshots(course_id=experiment.course_id, experiment_ids=[experiment.pk])
    return {"id": experiment.pk, "name": experiment.name, "classes": classes,
            "class_count": len(classes), "session_count": sessions.count(),
            "class_session_count": class_sessions(class_id).filter(version__experiment=experiment).count() if class_id else 0,
            "export_count": ExportJob.objects.filter(snapshot__in=snapshots).count(),
            "detail_url": f"/teacher/courses/{experiment.course_id}/experiments/{experiment.pk}/"}


def _scope_fingerprint(course, action, object_id, class_id, rows, sessions, snapshots, class_group=None):
    experiment_ids = [row["id"] for row in rows]
    value = {"course": course.pk, "course_updated_at": str(course.updated_at), "action": action,
             "object": object_id, "class": class_id, "rows": rows,
             "sessions": list(sessions.order_by("pk").values_list("pk", "status")),
             "exports": list(ExportJob.objects.filter(snapshot__in=snapshots).order_by("pk").values_list("pk", "snapshot_id")),
             "snapshots": list(snapshots.order_by("pk").values_list("pk", flat=True)),
             "experiments": list(experiments.Experiment.objects.filter(pk__in=experiment_ids)
                                 .order_by("pk").values_list("pk", "updated_at")),
             "plans": list(experiments.PublishPlan.objects.filter(experiment_id__in=experiment_ids)
                           .order_by("pk").values_list("pk", "plan_revision"))}
    if class_group:
        value["class_name"] = str(class_group)
        value["members"] = list(CourseEnrollment.objects.filter(class_group=class_group)
                                .order_by("pk").values_list("pk", "student_id", "is_active"))
        value["legacy_members"] = list(User.objects.filter(class_group=class_group).order_by("pk").values_list("pk", flat=True))
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _deletion_selection(*, course, action, experiment=None, class_group=None):
    objects = list(class_experiments(class_group).order_by('pk')) if action == 'delete_class' else [experiment]
    experiment_ids = [item.pk for item in objects]
    sessions = assessment.ExperimentSession.objects.filter(version__experiment_id__in=experiment_ids)
    tasks = experiments.ClassExperimentTask.objects.filter(experiment_id__in=experiment_ids)
    participants = experiments.ExperimentVersionParticipantSnapshot.objects.filter(version__experiment_id__in=experiment_ids)
    if class_group:
        sessions = sessions.filter(pk__in=class_sessions(class_group.pk))
        tasks = tasks.filter(class_group=class_group)
        participants = participants.filter(Q(class_task__in=tasks) | Q(class_snapshot__source_class_group=class_group))
    session_ids = list(sessions.order_by('pk').values_list('pk', flat=True))
    coverage_run_ids = _coverage_runs(session_ids)
    snapshots = affected_export_snapshots(course_id=course.pk, experiment_ids=experiment_ids,
        class_id=class_group.pk if class_group else None, coverage_run_ids=coverage_run_ids) if objects else ExportSnapshot.objects.none()
    return {'objects': objects, 'sessions': sessions, 'session_ids': session_ids,
            'task_ids': list(tasks.order_by('pk').values_list('pk', flat=True)),
            'participant_ids': list(participants.order_by('pk').values_list('pk', flat=True)),
            'coverage_run_ids': coverage_run_ids, 'snapshots': snapshots}


def deletion_preview(*, course, action, actor, experiment=None, class_group=None, _selection=None):
    require_course_manager(course, actor)
    selection = _selection or _deletion_selection(course=course, action=action, experiment=experiment, class_group=class_group)
    if action == "delete_class":
        objects = selection['objects']
        object_id, object_name = class_group.pk, str(class_group)
    else:
        objects = [experiment]
        object_id, object_name = experiment.pk, experiment.name
    class_id = class_group.pk if class_group else None
    rows = [_experiment_row(item, class_id) for item in objects]
    sessions, snapshots = selection['sessions'], selection['snapshots']
    blocked_reason = CLASS_BLOCKED if action == "delete_class" and rows else ""
    if action == "remove_class_experiment" and unattributed_sessions(course.pk).filter(version__experiment=experiment).exists():
        blocked_reason = "该实验存在无法确定所属班级的历史 Session，请使用“删除实验”清理整个实验。"
    allowed = not blocked_reason and course.status == course.Status.ACTIVE
    if not allowed and not blocked_reason:
        blocked_reason = "课程已归档，不能执行删除。"
    fingerprint = _scope_fingerprint(course, action, object_id, class_id, rows, sessions, snapshots, class_group)
    fingerprint = hashlib.sha256(json.dumps([fingerprint, selection['task_ids'], selection['participant_ids'],
        sorted(map(str, selection['coverage_run_ids']))], sort_keys=True).encode()).hexdigest()
    token = signing.dumps({"course_id": course.pk, "action": action, "object_id": object_id,
                           "class_id": class_id, "actor_id": actor.pk, "fingerprint": fingerprint}, salt=TOKEN_SALT, compress=True)
    return {"action": action, "object_id": object_id, "object_name": object_name,
            "allowed": allowed, "blocked_reason": blocked_reason, "experiments": rows,
            "counts": {"class_count": 1 if class_id else len({item["id"] for row in rows for item in row["classes"]}),
                       "session_count": sessions.count(), "export_count": ExportJob.objects.filter(snapshot__in=snapshots).count(),
                       "coverage_run_count": len(selection['coverage_run_ids']),
                       "coverage_session_count": RecordingCoverageSession.objects.filter(run_id__in=selection['coverage_run_ids']).count(),
                       "member_count": CourseEnrollment.objects.filter(class_group=class_group).count() if class_group else 0},
            "confirmation_token": token}


def invalidate_draft_publication_previews(*, experiment_ids, reason, actor=None):
    plans = experiments.PublishPlan.objects.select_for_update().filter(
        experiment_id__in=experiment_ids, experiment__status=experiments.Experiment.Status.DRAFT)
    ids = list(plans.values_list("pk", flat=True))
    plans.update(preview_run=None, plan_revision=F("plan_revision") + 1)
    plans.filter(status=experiments.PublishPlan.Status.PREVIEWED).update(status=experiments.PublishPlan.Status.DRAFT)
    # Discard the uncommitted preview items as well: their protected source-question
    # links must not prevent editing an otherwise unpublished draft.
    experiments.PublishPreviewRun.objects.filter(publish_plan_id__in=ids).delete()


def _raw_remove(queryset, counts):
    count = queryset.count()
    if count:
        counts[queryset.model._meta.label_lower] = counts.get(queryset.model._meta.label_lower, 0) + count
        queryset._raw_delete(queryset.db)


def _coverage_runs(session_ids):
    return list(RecordingCoverageRun.objects.filter(
        Q(session_results__session_id__in=session_ids) | Q(attempt_results__attempt__session_id__in=session_ids)
    ).distinct().values_list("pk", flat=True))


def _purge_sessions(session_ids, counts, coverage_run_ids):
    sessions = assessment.ExperimentSession.objects.select_for_update().filter(pk__in=session_ids).order_by("pk")
    list(sessions)
    attempts = assessment.Attempt.objects.select_for_update().filter(session_id__in=session_ids).order_by("pk")
    attempt_ids = list(attempts.values_list("pk", flat=True))
    # All incoming runtime references are explicitly scoped to these sessions or
    # attempts. Shared schema/contract artifacts and student identities survive.
    querysets = [
        assessment.PreparedSessionArtifact.objects.filter(session_id__in=session_ids),
        assessment.PreparedAttemptProjection.objects.filter(attempt_id__in=attempt_ids),
        assessment.PreparedActivationCancellation.objects.filter(session_id__in=session_ids),
        assessment.SessionSubmission.objects.filter(session_id__in=session_ids),
        assessment.NavigationReceipt.objects.filter(attempt_id__in=attempt_ids),
        assessment.CaptureMutationReceipt.objects.filter(attempt_id__in=attempt_ids),
        ResearchSessionResponse.objects.filter(materialization__session_id__in=session_ids),
        ResearchSessionVisit.objects.filter(materialization__session_id__in=session_ids),
        ResearchSessionBehavior.objects.filter(materialization__session_id__in=session_ids),
        ResearchSessionQuality.objects.filter(materialization__session_id__in=session_ids),
        ResearchSessionMaterialization.objects.filter(session_id__in=session_ids),
        RecordingCoverageAttempt.objects.filter(run_id__in=coverage_run_ids),
        RecordingCoverageSession.objects.filter(run_id__in=coverage_run_ids),
        experiments.ConditionAssignment.objects.filter(session_id__in=session_ids),
        assessment.ClientCaptureCheckpoint.objects.filter(session_id__in=session_ids),
        assessment.ResponseInputObservation.objects.filter(attempt_id__in=attempt_ids),
        assessment.GradeRecord.objects.filter(submission__attempt_id__in=attempt_ids),
        assessment.SubmissionEvent.objects.filter(attempt_id__in=attempt_ids),
        assessment.ConfidenceAssignment.objects.filter(attempt_id__in=attempt_ids),
        assessment.SimulationSnapshot.objects.filter(attempt_id__in=attempt_ids),
        assessment.DraftSaveEvent.objects.filter(attempt_id__in=attempt_ids),
        assessment.QuestionVisitEvent.objects.filter(session_id__in=session_ids),
    ]
    for queryset in querysets:
        _raw_remove(queryset, counts)
    attempts.update(active_response_event=None, final_response_event=None,
                    current_response_snapshot=None, final_response_snapshot=None)
    sessions.update(current_assignment=None)
    for queryset in [assessment.AssessmentEvent.objects.filter(session_id__in=session_ids),
                     assessment.ResponseSnapshot.objects.filter(attempt_id__in=attempt_ids),
                     assessment.Attempt.objects.filter(pk__in=attempt_ids),
                     assessment.StudentQuestionAssignment.objects.filter(session_id__in=session_ids),
                     assessment.ClientTimeSync.objects.filter(session_id__in=session_ids),
                     assessment.ExperimentSession.objects.filter(pk__in=session_ids),
                     RecordingCoverageRun.objects.filter(pk__in=coverage_run_ids)]:
        _raw_remove(queryset, counts)


def _purge_experiment_relations(experiment, counts, class_group=None):
    whole = class_group is None
    task_query = experiments.ClassExperimentTask.objects.filter(experiment=experiment)
    class_snapshots = experiments.ExperimentClassSnapshot.objects.filter(version__experiment=experiment)
    participants = experiments.ExperimentVersionParticipantSnapshot.objects.filter(version__experiment=experiment)
    roots = experiments.ExperimentClassRandomizationIdentity.objects.filter(experiment=experiment)
    plan_classes = experiments.PublishPlanClass.objects.filter(publish_plan__experiment=experiment)
    if not whole:
        task_query = task_query.filter(class_group=class_group)
        class_snapshots = class_snapshots.filter(source_class_group=class_group)
        participants = participants.filter(Q(class_task__in=task_query) | Q(class_snapshot__in=class_snapshots))
        roots = roots.filter(class_group=class_group)
        plan_classes = plan_classes.filter(class_group=class_group)
    task_ids = list(task_query.values_list("pk", flat=True))
    class_snapshot_ids = list(class_snapshots.values_list("pk", flat=True))
    root_ids = list(roots.values_list("pk", flat=True))
    plan_class_ids = list(plan_classes.values_list("pk", flat=True))
    removed_students = set(participants.values_list("student_id", flat=True))
    if class_group:
        removed_students.update(CourseEnrollment.objects.filter(class_group=class_group).values_list("student_id", flat=True))
    comparisons = StudentClassTaskComparison.objects.filter(class_task_id__in=task_ids)
    list(comparisons.select_for_update().order_by("pk").values_list("pk", flat=True))
    for queryset in [assessment.PreparedSessionRepair.objects.filter(participant__in=participants),
                     experiments.TaskManualClosure.objects.filter(class_task_id__in=task_ids),
                     StudentExperimentResultSnapshot.objects.filter(class_task_id__in=task_ids),
                     StudentTaskComparisonMember.objects.filter(comparison__in=comparisons), comparisons,
                     participants, experiments.ScheduleRevision.objects.filter(class_task_id__in=task_ids), task_query, class_snapshots]:
        _raw_remove(queryset, counts)
    preview_results = experiments.PublishPreviewClassResult.objects.filter(
        Q(randomization_identity_id__in=root_ids) | Q(publish_plan_class_id__in=plan_class_ids))
    result_ids = list(preview_results.values_list("pk", flat=True))
    for queryset in [experiments.PublishPreviewItem.objects.filter(participant__class_result_id__in=result_ids),
                     experiments.PublishPreviewParticipant.objects.filter(class_result_id__in=result_ids),
                     preview_results, experiments.PublishPlanExclusion.objects.filter(publish_plan_class_id__in=plan_class_ids),
                     plan_classes, roots]:
        _raw_remove(queryset, counts)
    if not whole:
        experiments.ExperimentParticipantClass.objects.filter(experiment=experiment, class_group=class_group).delete()
        other_students = experiments.ExperimentVersionParticipantSnapshot.objects.filter(version__experiment=experiment).values("student_id")
        experiments.ExperimentStudent.objects.filter(experiment=experiment, student_id__in=removed_students).exclude(student_id__in=other_students).delete()
        experiments.ExperimentParticipantOverride.objects.filter(experiment=experiment, student_id__in=removed_students).exclude(student_id__in=other_students).delete()
        # Invalidate all still-editable plans so an old confirmation cannot re-add
        # this class. Committed plans keep the unaffected classes' published facts.
        plans = experiments.PublishPlan.objects.filter(experiment=experiment, status__in=["draft", "previewed"])
        run_ids = list(plans.exclude(preview_run=None).values_list("preview_run_id", flat=True))
        plans.update(preview_run=None, plan_revision=F("plan_revision") + 1, status="draft")
        experiments.PublishPreviewRun.objects.filter(pk__in=run_ids).update(
            status="invalidated", invalidated_at=timezone.now(), invalidation_reason="class_removed")
        return
    versions = experiments.ExperimentVersion.objects.filter(experiment=experiment)
    snapshot_ids = list(experiments.QuestionSnapshot.objects.filter(version__in=versions).values_list("pk", flat=True))
    # The frozen-release back pointer is cleared only within this deletion service.
    from django.db.models import QuerySet
    QuerySet(model=experiments.Experiment, using=task_query.db).filter(pk=experiment.pk).update(frozen_release_snapshot=None)
    for queryset in [experiments.ReleaseSnapshotQuestionMapping.objects.filter(release_snapshot__in=versions),
                     experiments.ItemInstance.objects.filter(question_snapshot_id__in=snapshot_ids),
                     experiments.QuestionSnapshot.objects.filter(pk__in=snapshot_ids), versions]:
        _raw_remove(queryset, counts)
    experiments.PublishPlan.objects.filter(experiment=experiment).update(preview_run=None)
    # All PROTECT dependencies have now been removed explicitly; normal collection
    # can remove draft configuration, conditions and publication plans.
    experiment.delete()


@contextmanager
def _lock_export_generations(*, course, experiment, class_group):
    from apps.analytics.services import _export_file_lock

    with ExitStack() as locks:
        if experiment is not None:
            selection = _deletion_selection(course=course, action='remove_class_experiment' if class_group else 'delete_experiment',
                experiment=experiment, class_group=class_group)
            snapshots = selection['snapshots']
            job_ids = list(ExportJob.objects.filter(snapshot__in=snapshots).order_by("pk")
                           .values_list("pk", flat=True))
            # Exporters need the course lock to finalize; wait before taking it,
            # leaving their lease rows writable by the heartbeat thread.
            # The confirmation fingerprint rechecks the export set after waiting.
            for job_id in job_ids:
                try:
                    locks.enter_context(_export_file_lock(job_id, blocking=False))
                except BlockingIOError as error:
                    raise DeletionConflict('研究导出正在生成，请稍后重新核对并删除。', code='DELETION_BUSY') from error
        yield


def execute_deletion(*, request, course, action, confirmation_token, experiment=None, class_group=None):
    actor = request.user
    with _lock_export_generations(course=course, experiment=experiment, class_group=class_group), _deletion_transaction():
        course = require_writable_course(type(course).objects.select_for_update(nowait=True).get(pk=course.pk))
        require_course_manager(course, actor)
        ids = list(class_experiments(class_group).values_list("pk", flat=True)) if action == "delete_class" else [experiment.pk]
        locked = list(experiments.Experiment.objects.select_for_update(nowait=True).filter(pk__in=ids).order_by("pk"))
        if experiment:
            if not locked:
                raise DeletionConflict("实验已被删除，请刷新列表。")
            experiment = locked[0]
        if class_group:
            class_group = ClassGroup.objects.select_for_update(nowait=True).get(pk=class_group.pk, course=course)
        selection = _deletion_selection(course=course, action=action, experiment=experiment, class_group=class_group)
        for queryset in [experiments.ClassExperimentTask.objects.filter(pk__in=selection['task_ids']),
                         experiments.ExperimentVersionParticipantSnapshot.objects.filter(pk__in=selection['participant_ids']),
                         assessment.ExperimentSession.objects.filter(pk__in=selection['session_ids']),
                         assessment.PreparedSessionRepair.objects.filter(participant_id__in=selection['participant_ids']),
                         assessment.SessionSubmission.objects.filter(session_id__in=selection['session_ids']),
                         ResearchSessionMaterialization.objects.filter(session_id__in=selection['session_ids']),
                         assessment.Attempt.objects.filter(session_id__in=selection['session_ids'])]:
            list(queryset.select_for_update(nowait=True).order_by('pk').values_list('pk', flat=True))
        if (assessment.SessionSubmission.objects.filter(session_id__in=selection['session_ids'], status='running').exists()
                or ResearchSessionMaterialization.objects.filter(session_id__in=selection['session_ids'], status='running').exists()):
            raise DeletionConflict('交卷或研究整理任务正在执行，请稍后重新核对并删除。', code='DELETION_BUSY')
        preview = deletion_preview(course=course, action=action, actor=actor, experiment=experiment, class_group=class_group, _selection=selection)
        if not preview["allowed"]:
            raise DeletionConflict(preview["blocked_reason"])
        try:
            previous = signing.loads(confirmation_token, salt=TOKEN_SALT, max_age=1800)
            current = signing.loads(preview["confirmation_token"], salt=TOKEN_SALT)
        except signing.BadSignature as exc:
            raise DeletionConflict("删除确认已失效，请刷新影响范围后重新确认。") from exc
        if previous != current:
            raise DeletionConflict("删除影响范围已变化，请刷新后重新确认。")
        if action == "delete_class":
            from apps.accounts.class_management_services import delete_course_class_group
            details = delete_course_class_group(request=request, course=course, class_group=class_group)
            result = {"deleted": True, "cleanup_pending": False, "cleanup_id": None}
        else:
            sessions = assessment.ExperimentSession.objects.filter(version__experiment=experiment)
            if class_group:
                sessions = sessions.filter(pk__in=class_sessions(class_group.pk))
            session_ids = selection['session_ids']
            coverage_run_ids = selection['coverage_run_ids']
            blobs = list(assessment.SimulationSnapshot.objects.filter(attempt__session_id__in=session_ids)
                         .values_list("blob_storage_key", flat=True))
            shared_blobs = set(assessment.SimulationSnapshot.objects.exclude(attempt__session_id__in=session_ids)
                               .filter(blob_storage_key__in=blobs).values_list("blob_storage_key", flat=True))
            result = retire_research_exports(course_id=course.pk, experiment_ids=[experiment.pk],
                class_id=class_group.pk if class_group else None, coverage_run_ids=coverage_run_ids,
                reason=action, actor=actor,
                extra_files=[{"kind": "storage", "path": key} for key in blobs if key not in shared_blobs])
            counts = {}
            _purge_sessions(session_ids, counts, coverage_run_ids)
            _purge_experiment_relations(experiment, counts, class_group)
            details = {"experiment_id": preview["object_id"], "course_id": course.pk,
                       "class_id": class_group.pk if class_group else None,
                       "counts": counts, "export_count": result["export_count"], "cleanup_id": result["cleanup_id"]}
            log_action(request, "experiment.delete" if action == "delete_experiment" else "experiment.remove_from_class", details=details)
            result["deleted"] = True
    result.update(cleanup_state(result["cleanup_id"]))
    return result
