"""Read-only result versions: no answer loading, cache rebuild or exposure write."""
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import F, OuterRef, Subquery
from django.db.models.fields.json import KeyTransform
from django.utils import timezone

from apps.accounts.models import ClassStudentAnalyticsPolicy, User
from apps.assessment.feedback_services import feedback_availability
from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.experiments.models import ClassExperimentTask, Experiment
from .models import StudentClassLearningComparison, StudentClassTaskComparison, StudentLearningComparisonMember
from .student_comparisons import COMPARISON_PROJECTION_VERSION, comparison_metadata, comparison_source_signatures, shared_results_enabled
from .teaching.scope import analytics_read_transaction


@contextmanager
def _status_read_transaction():
    # A caller-owned transaction (including TestCase) already established its
    # isolation level. PostgreSQL cannot change it after the caller's queries.
    if connection.in_atomic_block:
        yield timezone.now()
    else:
        with analytics_read_transaction() as at:
            yield at


def student_result_status(enrollment, *, experiment_id=None, task_id=None, question_id=None, require_results=True):
    from apps.accounts.models import CourseEnrollment
    with _status_read_transaction() as at:
        policy_fields = ('show_scores', 'show_correct_rate', 'show_item_results', 'show_class_average', 'rank_mode', 'show_kc_rates', 'show_trend')
        try:
            current = CourseEnrollment.objects.select_related('course', 'class_group__student_analytics_policy', 'student').only(
                'pk', 'course_id', 'student_id', 'class_group_id', 'student__role', 'student__is_active',
                'course__name', 'course__code', 'course__term', 'class_group__name', 'class_group__course_id',
                'class_group__student_analytics_policy__id', 'class_group__student_analytics_policy__updated_at',
                *['class_group__student_analytics_policy__' + field for field in policy_fields],
            ).get(pk=enrollment.pk, student_id=enrollment.student_id, is_active=True)
        except CourseEnrollment.DoesNotExist as error:
            raise LookupError('Student enrollment is unavailable.') from error
        if (current.class_group.course_id != current.course_id or
                current.student.role != User.Role.STUDENT or not current.student.is_active):
            raise LookupError('Student enrollment is unavailable.')
        policy = current.class_group.student_analytics_policy
        tasks = ClassExperimentTask.objects.filter(
            class_group_id=current.class_group_id, experiment__course_id=current.course_id,
            experiment__status__in=(Experiment.Status.FROZEN, Experiment.Status.PUBLISHED, Experiment.Status.CLOSED),
            release_snapshot__experiment_id=F('experiment_id'),
            class_snapshot__version_id=F('release_snapshot_id'),
            class_snapshot__source_class_group_id=F('class_group_id'),
            published_at=F('release_snapshot__published_at'),
            class_snapshot__participant_snapshots__student_id=current.student_id,
        ).select_related('experiment', 'release_snapshot').only(
            'pk', 'class_group_id', 'experiment_id', 'release_snapshot_id', 'class_snapshot_id',
            'published_at', 'cancelled_at', 'manual_closed_at', 'invalidated_at', 'feedback_released_at', 'result_revision',
            'experiment__name', 'experiment__course_id', 'experiment__status',
            'release_snapshot__configuration', 'release_snapshot__definition_fingerprint',
        ).order_by('pk')
        if experiment_id is not None:
            tasks = tasks.filter(experiment_id=experiment_id)
        if task_id is not None:
            tasks = tasks.filter(pk=task_id)
        tasks = tuple(tasks)
        detail = experiment_id is not None or task_id is not None
        if detail and not tasks:
            raise LookupError('Experiment is outside the student cohort.')
        # Only own scalar session facts; no attempts or submissions are loaded.
        sessions = ExperimentSession.objects.filter(student_id=current.student_id,
            version_id__in=[task.release_snapshot_id for task in tasks],
            class_snapshot_id__in=[task.class_snapshot_id for task in tasks],
        ).exclude(status=ExperimentSession.Status.INVALIDATED).only(
            'version_id', 'class_snapshot_id', 'status', 'result_revision', 'terminal_at', 'completed_at',
        ).order_by('pk')
        by_pair = {(s.version_id, s.class_snapshot_id): s for s in sessions}
        allowed = []
        dependencies = []
        completed_at = None
        for task in tasks:
            session = by_pair.get((task.release_snapshot_id, task.class_snapshot_id))
            availability = feedback_availability(task, session, at=at)
            released = availability.released and availability.feedback_mode in (Experiment.Feedback.FULL, Experiment.Feedback.CORRECTNESS)
            if detail and require_results and not released:
                raise PermissionDenied('该实验未开放结果反馈。')
            if released:
                allowed.append(task)
            if detail and session is not None:
                completed_at = session.terminal_at or session.completed_at
            dependencies.append((task.pk, task.result_revision, task.experiment.name,
                task.release_snapshot.definition_fingerprint, task.release_snapshot.configuration,
                task.feedback_released_at, released,
                (session.pk, session.result_revision, session.status, session.terminal_at) if session else None))
        if question_id is not None:
            if not policy.show_item_results:
                raise PermissionDenied('教师未开放逐题结果。')
            session = by_pair.get((tasks[0].release_snapshot_id, tasks[0].class_snapshot_id))
            if session is None or not SubmissionEvent.objects.filter(
                attempt__session_id=session.pk, attempt__assignment__snapshot_id=question_id,
                attempt__assignment__snapshot__version_id=tasks[0].release_snapshot_id,
            ).exists():
                raise LookupError('Student has no formal submission for this question.')
        metadata = {}
        summary_revisions = []
        own_task_revisions = None
        needs_comparison = policy.show_class_average or (detail and policy.rank_mode == ClassStudentAnalyticsPolicy.RankMode.SELF)
        if question_id is None and shared_results_enabled(current.course_id) and needs_comparison:
            signatures = comparison_source_signatures(allowed)
            rows = {row['class_task_id']: SimpleNamespace(**row) for row in
                StudentClassTaskComparison.objects.filter(class_task_id__in=[t.pk for t in allowed]).values(
                    'class_task_id', 'source_signature', 'status', 'generated_at', 'projection_version')}
            metadata = comparison_metadata(allowed, rows, signatures=signatures)
            summary_revisions = [(pk, row.source_signature, signatures.get(pk), row.status, row.projection_version, row.generated_at) for pk, row in sorted(rows.items())]
            if not detail and metadata.get('comparison_status') == 'ready':
                # Parent status and this indexed student's revision slice share one SQL snapshot.
                own = StudentLearningComparisonMember.objects.filter(comparison_id=OuterRef('pk'), student_id=current.student_id).annotate(
                    revisions=KeyTransform('task_signatures', 'payload')).values('revisions')[:1]
                learning = StudentClassLearningComparison.objects.filter(class_group_id=current.class_group_id).annotate(
                    own_revisions=Subquery(own)).values('status', 'projection_version', 'generated_at', 'own_revisions').first()
                own_task_revisions = learning['own_revisions'] if learning else None
                if (not learning or learning['projection_version'] != COMPARISON_PROJECTION_VERSION):
                    metadata = {'comparison_status': 'pending'}
                elif learning['status'] == 'failed':
                    metadata = {'comparison_status': 'failed'}
                elif learning['status'] != 'ready' or own_task_revisions != {str(t.pk): signatures[t.pk] for t in allowed}:
                    metadata = {'comparison_status': 'updating'}
                else:
                    metadata['comparison_generated_at'] = learning['generated_at']
        source = [current.pk, current.course_id, current.class_group_id, current.student_id,
            current.course.name, current.course.code, current.course.term, current.class_group.name,
            policy.pk, policy.updated_at, [getattr(policy, field) for field in policy_fields],
            dependencies, summary_revisions, own_task_revisions, metadata.get('comparison_status'), question_id]
        # Course refresh timestamps alone do not invalidate unchanged result content.
        version = hashlib.sha256(json.dumps(source, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
        result = {'version': version, **metadata}
        if detail:
            result['completed_at'] = completed_at.isoformat() if completed_at else None
        return result
