"""Bounded singleflight generation of shared class-task comparison summaries."""
from datetime import timedelta
from contextlib import nullcontext
from decimal import Decimal
import hashlib
import json
import logging
from types import SimpleNamespace

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, OuterRef, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from apps.accounts.models import CourseEnrollment, User
from apps.assessment.models import ExperimentSession
from apps.experiments.models import ClassExperimentTask
from .models import (StudentClassTaskComparison, StudentClassLearningComparison,
                     StudentTaskComparisonMember, StudentLearningComparisonMember)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
COMPARISON_PROJECTION_VERSION = 2


def shared_results_enabled(course_id):
    allowed = settings.STUDENT_SHARED_RESULTS_COURSE_IDS
    return settings.STUDENT_SHARED_RESULTS_ENABLED and ("*" in allowed or str(course_id) in allowed)


def _enabled_tasks():
    tasks = ClassExperimentTask.objects.all()
    allowed = settings.STUDENT_SHARED_RESULTS_COURSE_IDS
    if "*" not in allowed:
        tasks = tasks.filter(experiment__course_id__in=allowed)
    return tasks


def bump_result_revision(session, *, increment=1):
    """Call inside the same transaction that writes the result fact."""
    ExperimentSession.objects.filter(pk=session.pk).update(result_revision=F('result_revision') + increment)
    session.result_revision += increment


def comparison_source_signatures(tasks):
    """Batch only scalar dependencies; callers own the consistent read snapshot.

    Class revisions cover eligibility changes. Personal revisions cover result
    facts without a shared write. Include every session (even invalidated ones)
    so membership of the source and recovery cannot alias an old signature.
    """
    task_ids = [task.pk for task in tasks]
    if not task_ids:
        return {}
    task_rows = list(ClassExperimentTask.objects.filter(pk__in=task_ids).order_by('pk').values_list(
        'pk', 'release_snapshot_id', 'class_snapshot_id', 'result_revision',
        'class_group_id', 'experiment_id', 'published_at', 'cancelled_at',
        'invalidated_at', 'manual_closed_at', 'feedback_released_at',
    ))
    by_pair = {(row[1], row[2]): [] for row in task_rows}
    sessions = ExperimentSession.objects.filter(
        version_id__in={row[1] for row in task_rows},
        class_snapshot_id__in={row[2] for row in task_rows},
    ).order_by('pk').values_list('version_id', 'class_snapshot_id', 'pk', 'student_id',
                               'result_revision', 'status', 'terminal_at', 'recovery_round')
    for version_id, snapshot_id, *source in sessions:
        if (version_id, snapshot_id) in by_pair:
            by_pair[(version_id, snapshot_id)].append(source)
    return {row[0]: hashlib.sha256(json.dumps(
        [COMPARISON_PROJECTION_VERSION, row, by_pair[(row[1], row[2])]],
        default=str, separators=(',', ':'),
    ).encode()).hexdigest() for row in task_rows}


def read_comparisons(tasks, *, student_id):
    # Parent and indexed own member are read in one SQL/MVCC snapshot. Accessing
    # a JSON path in the cohort payload would still detoast the whole document.
    own_rank = StudentTaskComparisonMember.objects.filter(
        comparison_id=OuterRef("pk"), student_id=student_id).values("rank")[:1]
    rows = StudentClassTaskComparison.objects.filter(class_task_id__in=[task.pk for task in tasks]).annotate(
        own_rank=Subquery(own_rank),
    ).values("class_task_id", "source_revision", "source_signature", "status", "generated_at", "projection_version", "means", "full", "own_rank")
    return {row["class_task_id"]: SimpleNamespace(**{key: value for key, value in row.items() if key not in ("means", "full", "own_rank")}, payload={"means": row["means"] or {}, "full": row["full"] or {}, "own_rank": row["own_rank"]}) for row in rows}


def read_learning_comparison(class_group_id, student_id):
    own = StudentLearningComparisonMember.objects.filter(
        comparison_id=OuterRef("pk"), student_id=student_id).values("payload")[:1]
    row = StudentClassLearningComparison.objects.filter(class_group_id=class_group_id).annotate(
        own=Subquery(own),
    ).values("own", "generated_at", "status", "projection_version").first()
    return row


def learning_payload(tasks, row, *, signatures=None):
    if row is None or row["status"] != "ready" or row["projection_version"] != COMPARISON_PROJECTION_VERSION or not row["own"]:
        return None
    signatures = comparison_source_signatures(tasks) if signatures is None else signatures
    expected = {str(task.pk): signatures.get(task.pk) for task in tasks}
    if row["own"].get("task_signatures") != expected:
        return None
    return row["own"]


def learning_metadata(tasks, rows, learning_row, *, signatures=None):
    signatures = comparison_source_signatures(tasks) if signatures is None else signatures
    result = comparison_metadata(tasks, rows, signatures=signatures)
    if result.get("comparison_status") == "ready":
        if learning_payload(tasks, learning_row, signatures=signatures) is None:
            if learning_row and learning_row["status"] == "failed":
                state = "failed"
            elif learning_row is None or learning_row["projection_version"] != COMPARISON_PROJECTION_VERSION:
                state = "pending"
            else:
                state = "updating"
            result = {"comparison_status": state}
        else:
            result["comparison_generated_at"] = learning_row["generated_at"]
    return result


def comparison_metadata(tasks, rows, *, signatures=None):
    if not tasks:
        return {}
    signatures = comparison_source_signatures(tasks) if signatures is None else signatures
    states = []
    dates = []
    for task in tasks:
        row = rows.get(task.pk)
        if row is None:
            states.append('pending')
        elif row.status == 'ready' and row.projection_version != COMPARISON_PROJECTION_VERSION:
            states.append('pending')
        elif row.source_signature == signatures.get(task.pk) and row.status == 'ready':
            states.append('ready')
            dates.append(row.generated_at)
        elif row.status == 'failed' and row.source_signature == signatures.get(task.pk):
            states.append('failed')
        else:
            states.append('updating' if row.generated_at else 'pending')
    state = next((value for value in ('failed', 'updating', 'pending') if value in states), 'ready')
    result = {'comparison_status': state}
    if dates and state == 'ready':
        result['comparison_generated_at'] = min(dates)
    return result


def ready_payload(task, rows, *, signatures=None):
    row = rows.get(task.pk)
    if row is None or row.status != 'ready' or row.projection_version != COMPARISON_PROJECTION_VERSION:
        return None
    signatures = comparison_source_signatures((task,)) if signatures is None else signatures
    if row.source_signature == signatures.get(task.pk):
        return row.payload
    return None


def _refresh_task_comparison(task_id):
    from .teaching.scope import analytics_read_transaction
    # Called after the claim transaction commits, never under its write lock.
    with analytics_read_transaction():
        task = ClassExperimentTask.objects.select_related('experiment').get(pk=task_id)
        signature = comparison_source_signatures((task,))[task.pk]
        return _build_summary(task), signature, task.result_revision


def _build_summary(task):
    from .teaching.student_projection import _load_bundle, _task_attempts, _task_grades
    enrollment = CourseEnrollment.objects.filter(course_id=task.experiment.course_id, class_group_id=task.class_group_id, is_active=True, student__is_active=True, student__role=User.Role.STUDENT, student__experiment_version_participant_snapshots__class_snapshot_id=task.class_snapshot_id).first()
    if enrollment is None:
        return {'students': {}}
    bundle = _load_bundle(enrollment, timezone.now(), experiment_id=task.experiment_id)
    students = {}
    for student_id in bundle.eligible_by_task[task.pk]:
        grades = _task_grades(bundle, task, student_id)
        attempts = _task_attempts(bundle, task, student_id)
        correct = [grade.is_correct for grade, _ in grades if grade.is_correct is not None]
        scored = [(grade.score, grade.max_score) for grade, _ in grades if grade.score is not None and grade.max_score is not None]
        earned = sum((score for score, _ in scored), Decimal('0'))
        maximum = sum((max_score for _, max_score in scored), Decimal('0'))
        session = bundle.session_by_task_student.get((task.pk, student_id))
        fully_graded = bool(attempts) and session is not None and session.status == ExperimentSession.Status.COMPLETED and len(scored) == len(attempts) and all(attempt.pk in bundle.submission_by_attempt for attempt in attempts) and maximum > 0
        kcs = {}
        for grade, attempt in grades:
            if grade.is_correct is None:
                continue
            for kc in dict.fromkeys(attempt.assignment.snapshot.all_kcs or [attempt.assignment.snapshot.primary_kc]):
                counter = kcs.setdefault(kc, [0, 0])
                counter[0] += int(grade.is_correct is True)
                counter[1] += 1
        students[str(student_id)] = {'current': student_id in bundle.current_student_ids, 'correct': [sum(value is True for value in correct), len(correct)], 'score': [str(earned), str(maximum)], 'fully_graded': fully_graded, 'session_status': session.status if session else None, 'kcs': kcs}
    return _summary_values(students)


def _means(counters):
    ratios = [Decimal(str(value[0])) / Decimal(str(value[1])) for value in counters if Decimal(str(value[1])) > 0]
    return [str(sum(ratios, Decimal("0"))), len(ratios)]


def _summary_values(students):
    full_ratios = {student_id: Decimal(row["score"][0]) / Decimal(row["score"][1]) for student_id, row in students.items() if row["fully_graded"]}
    rank_positions = {}
    for index, ratio in enumerate(sorted(full_ratios.values(), reverse=True), start=1):
        rank_positions.setdefault(ratio, index)
    return {"students": students,
        "means": {kind: _means([row[kind] for row in students.values() if row["current"]]) for kind in ("correct", "score")},
        "full": {"mean": str(sum(full_ratios.values(), Decimal("0")) / len(full_ratios)) if full_ratios else None, "sample_size": len(full_ratios)},
        "ranks": {student_id: {"position": rank_positions[own], "sample_size": len(full_ratios)} for student_id, own in full_ratios.items()},
    }


def _learning_source_signature(class_group_id, *, at):
    from apps.assessment.feedback_services import feedback_availability
    from apps.assessment.deadline_services import DeadlineDecisionService
    tasks = tuple(ClassExperimentTask.objects.filter(class_group_id=class_group_id).select_related('release_snapshot').only(
        'pk', 'result_revision', 'release_snapshot_id', 'class_snapshot_id', 'experiment_id',
        'cancelled_at', 'invalidated_at', 'manual_closed_at', 'feedback_released_at', 'published_at',
        'release_snapshot__configuration',
    ).order_by('pk'))
    signatures = comparison_source_signatures(tasks)
    task_dependencies = []
    for task in tasks:
        schedule = DeadlineDecisionService.schedule_at(task, at)
        task.latest_schedule_revisions = [schedule] if schedule is not None else []
        schedule_dependency = (schedule.pk, schedule.revision_no, schedule.opens_at, schedule.closes_at, schedule.reopens_manual_close) if schedule else None
        decisions = [feedback_availability(task, SimpleNamespace(status=status), at=at).released
            for status in (ExperimentSession.Status.COMPLETED, ExperimentSession.Status.EXPIRED, ExperimentSession.Status.IN_PROGRESS)]
        task_dependencies.append((task.pk, signatures[task.pk], task.release_snapshot_id, str(task.class_snapshot_id),
            task.release_snapshot.configuration, task.cancelled_at, task.invalidated_at, task.manual_closed_at,
            task.feedback_released_at, schedule_dependency, decisions))
    summaries = list(StudentClassTaskComparison.objects.filter(class_task__class_group_id=class_group_id).order_by('class_task_id').values_list(
        'class_task_id', 'source_signature', 'status', 'projection_version', 'generated_at'))
    source = [COMPARISON_PROJECTION_VERSION, task_dependencies, summaries]
    return hashlib.sha256(json.dumps(source, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def _refresh_learning_comparison(class_group_id):
    from .teaching.scope import analytics_read_transaction
    # Reuse caller-owned isolation in nested transactions; workers establish
    # their own read-only repeatable snapshot outside the short claim lease.
    boundary = nullcontext(timezone.now()) if connection.in_atomic_block else analytics_read_transaction()
    with boundary as at:
        payload = _build_learning_comparison(class_group_id, at=at)
        payload['source_signature'] = _learning_source_signature(class_group_id, at=at)
        return payload


def _build_learning_comparison(class_group_id, *, at):
    from apps.assessment.feedback_services import feedback_availability
    tasks = list(ClassExperimentTask.objects.filter(class_group_id=class_group_id).select_related("release_snapshot", "experiment"))
    signatures = comparison_source_signatures(tasks)
    rows = {row.class_task_id: row for row in StudentClassTaskComparison.objects.filter(class_task_id__in=[task.pk for task in tasks])}
    usable = {task.pk: rows[task.pk].payload for task in tasks if ready_payload(task, rows, signatures=signatures) is not None}
    student_ids = set().union(*(payload.get("students", {}).keys() for payload in usable.values())) if usable else set()
    output = {}
    now = at
    subsets = {}
    release_decisions = {}
    for task in tasks:
        statuses = {row["session_status"] for row in usable.get(task.pk, {}).get("students", {}).values()}
        for status in statuses:
            release_decisions[(task.pk, status)] = feedback_availability(task, SimpleNamespace(status=status), at=now).released
    for student_id in student_ids:
        allowed = [task for task in tasks if task.pk in usable and student_id in usable[task.pk].get("students", {}) and release_decisions[(task.pk, usable[task.pk]["students"][student_id]["session_status"])]]
        subset_key = tuple((task.pk, signatures[task.pk]) for task in allowed)
        if subset_key in subsets:
            output[student_id] = subsets[subset_key]
            continue
        counters = {kind: {} for kind in ("correct", "score")}
        kc_counters = {}
        for task in allowed:
            for candidate_id, candidate in usable[task.pk]["students"].items():
                if not candidate["current"]:
                    continue
                for kind in counters:
                    total = counters[kind].setdefault(candidate_id, [Decimal("0"), Decimal("0")])
                    total[0] += Decimal(str(candidate[kind][0]))
                    total[1] += Decimal(str(candidate[kind][1]))
                for kc, values in candidate["kcs"].items():
                    total = kc_counters.setdefault(kc, {}).setdefault(candidate_id, [0, 0])
                    total[0] += values[0]
                    total[1] += values[1]
        output[student_id] = {"task_signatures": {str(task.pk): signatures[task.pk] for task in allowed}, "means": {kind: _means(list(values.values())) for kind, values in counters.items()}, "kc_means": {kc: _means(list(values.values())) for kc, values in kc_counters.items()}}
        subsets[subset_key] = output[student_id]
    return {"students": output}


def run_comparison_jobs(*, limit=1):
    if not settings.STUDENT_SHARED_RESULTS_ENABLED:
        return {"completed": 0, "failed": 0}
    limit = max(1, min(int(limit), 50))
    enabled_ids = _enabled_tasks().values("pk")
    now = timezone.now()
    # Reconcile scalar source versions; never load answers or grades here.
    missing = _enabled_tasks().filter(student_comparison__isnull=True).order_by('pk')[:limit]
    StudentClassTaskComparison.objects.bulk_create([StudentClassTaskComparison(class_task=task, source_revision=task.result_revision) for task in missing], ignore_conflicts=True)
    from .teaching.scope import analytics_read_transaction
    with analytics_read_transaction():
        signatures = comparison_source_signatures(_enabled_tasks().only('pk'))
        sources = list(StudentClassTaskComparison.objects.filter(class_task_id__in=enabled_ids).exclude(status='running').values(
            'pk', 'class_task_id', 'source_signature'))
    for source in sources:
        if source['source_signature'] != signatures[source['class_task_id']]:
            StudentClassTaskComparison.objects.filter(pk=source['pk'], source_signature=source['source_signature']).exclude(status='running').update(
                status='queued', attempts=0, next_attempt_at=now)
    StudentClassTaskComparison.objects.filter(class_task_id__in=enabled_ids, status='ready').exclude(projection_version=COMPARISON_PROJECTION_VERSION).update(status='queued', attempts=0, next_attempt_at=now)
    expired = StudentClassTaskComparison.objects.filter(class_task_id__in=enabled_ids, status='running', started_at__lt=now-timedelta(seconds=LEASE_SECONDS))
    expired.filter(attempts__gte=MAX_ATTEMPTS).update(status='failed', error_code='lease_exhausted')
    expired.filter(attempts__lt=MAX_ATTEMPTS).update(status='queued', next_attempt_at=now)
    completed = failed = 0
    for _ in range(limit):
        with transaction.atomic():
            row = StudentClassTaskComparison.objects.select_for_update(skip_locked=True).filter(class_task_id__in=enabled_ids, status='queued', next_attempt_at__lte=timezone.now(), attempts__lt=MAX_ATTEMPTS).order_by('next_attempt_at','pk').first()
            if row is None:
                break
            task = ClassExperimentTask.objects.select_related('experiment').get(pk=row.class_task_id)
            row.source_revision = task.result_revision
            row.source_signature = comparison_source_signatures((task,))[task.pk]
            row.status = 'running'
            row.started_at = timezone.now()
            row.attempts += 1
            row.save(update_fields=['source_revision', 'source_signature', 'status', 'started_at', 'attempts'])
            lease = row.started_at
        try:
            payload, source_signature, source_revision = _refresh_task_comparison(task.pk)
            with analytics_read_transaction():
                latest_signature = comparison_source_signatures((task,))[task.pk]
            with transaction.atomic():
                current = StudentClassTaskComparison.objects.select_for_update().get(pk=row.pk)
                if current.status != 'running' or current.started_at != lease:
                    continue
                if latest_signature != source_signature:
                    current.status = 'queued'
                    current.attempts = 0
                    current.next_attempt_at = timezone.now()
                else:
                    current.source_signature = source_signature
                    current.source_revision = source_revision
                    StudentTaskComparisonMember.objects.filter(comparison=current).delete()
                    StudentTaskComparisonMember.objects.bulk_create([
                        StudentTaskComparisonMember(comparison=current, student_id=student_id,
                                                    rank=payload.get('ranks', {}).get(student_id))
                        for student_id in payload.get('students', {})
                    ], batch_size=1000)
                    current.payload = payload
                    current.means = payload.get('means', {})
                    current.full = payload.get('full', {})
                    current.projection_version = COMPARISON_PROJECTION_VERSION
                    current.generated_at = timezone.now()
                    current.status = 'ready'
                    current.error_code = ''
                    completed += 1
                current.save()
            if current.status == 'ready':
                _enqueue_learning_comparison(task.class_group_id)
        except Exception:
            logger.exception('Student comparison generation failed for task %s', task.pk)
            with transaction.atomic():
                current = StudentClassTaskComparison.objects.select_for_update().get(pk=row.pk)
                if current.status == 'running' and current.started_at == lease:
                    current.status = 'failed' if current.attempts >= MAX_ATTEMPTS else 'queued'
                    current.error_code = 'generation_failed'
                    current.next_attempt_at = timezone.now() + timedelta(seconds=5 * current.attempts)
                    current.save()
            failed += 1
    _run_learning_comparison_jobs(limit=limit)
    return {'completed': completed, 'failed': failed}


def _enqueue_learning_comparison(class_group_id):
    with transaction.atomic():
        row, _ = StudentClassLearningComparison.objects.select_for_update().get_or_create(class_group_id=class_group_id)
        if row.status != "running":
            row.status = "queued"
            row.attempts = 0
            row.next_refresh_at = timezone.now()
            row.save(update_fields=["status", "attempts", "next_refresh_at"])


def _run_learning_comparison_jobs(*, limit):
    now = timezone.now()
    allowed_classes = _enabled_tasks().values("class_group_id")
    StudentClassLearningComparison.objects.filter(class_group_id__in=allowed_classes, status="ready").exclude(projection_version=COMPARISON_PROJECTION_VERSION).update(status="queued", attempts=0, next_refresh_at=now)
    expired = StudentClassLearningComparison.objects.filter(class_group_id__in=allowed_classes, status="running", started_at__lt=now-timedelta(seconds=LEASE_SECONDS))
    expired.filter(attempts__gte=MAX_ATTEMPTS).update(status="failed", error_code="lease_exhausted")
    expired.filter(attempts__lt=MAX_ATTEMPTS).update(status="queued", next_refresh_at=now)
    for _ in range(limit):
        with transaction.atomic():
            row = StudentClassLearningComparison.objects.select_for_update(skip_locked=True).defer('payload').annotate(
                cached_signature=KeyTextTransform('source_signature', 'payload'),
            ).filter(class_group_id__in=allowed_classes, status__in=("queued", "ready"), next_refresh_at__lte=timezone.now(), attempts__lt=MAX_ATTEMPTS).order_by("next_refresh_at", "pk").first()
            if row is None:
                break
            source_signature = _learning_source_signature(row.class_group_id, at=timezone.now())
            if row.projection_version == COMPARISON_PROJECTION_VERSION and row.cached_signature == source_signature:
                row.status = 'ready'
                row.attempts = 0
                row.error_code = ''
                row.next_refresh_at = timezone.now()+timedelta(seconds=15)
                row.save(update_fields=['status', 'attempts', 'error_code', 'next_refresh_at'])
                continue
            row.status = "running"
            row.started_at = timezone.now()
            row.attempts += 1
            row.save(update_fields=["status", "started_at", "attempts"])
            lease = row.started_at
        try:
            payload = _refresh_learning_comparison(row.class_group_id)
            with transaction.atomic():
                current = StudentClassLearningComparison.objects.select_for_update().get(pk=row.pk)
                if current.status != "running" or current.started_at != lease:
                    continue
                if payload.get('source_signature') != _learning_source_signature(row.class_group_id, at=timezone.now()):
                    current.status = 'queued'
                    current.attempts = 0
                    current.next_refresh_at = timezone.now()
                    current.save(update_fields=['status', 'attempts', 'next_refresh_at'])
                    continue
                StudentLearningComparisonMember.objects.filter(comparison=current).delete()
                StudentLearningComparisonMember.objects.bulk_create([
                    StudentLearningComparisonMember(comparison=current, student_id=student_id, payload=own)
                    for student_id, own in payload.get("students", {}).items()
                ], batch_size=1000)
                current.payload = payload
                current.projection_version = COMPARISON_PROJECTION_VERSION
                current.generated_at = timezone.now()
                current.next_refresh_at = timezone.now()+timedelta(seconds=15)
                current.status = "ready"
                current.attempts = 0
                current.error_code = ""
                current.save()
        except Exception:
            logger.exception("Student learning comparison generation failed for class %s", row.class_group_id)
            with transaction.atomic():
                current = StudentClassLearningComparison.objects.select_for_update().get(pk=row.pk)
                if current.status == "running" and current.started_at == lease:
                    current.status = "failed" if current.attempts >= MAX_ATTEMPTS else "queued"
                    current.next_refresh_at = timezone.now()+timedelta(seconds=5*current.attempts)
                    current.error_code = "generation_failed"
                    current.save()
