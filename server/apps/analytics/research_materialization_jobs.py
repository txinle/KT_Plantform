"""Scheduling and operational metrics for session research projections."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.utils import timezone

from apps.assessment.models import ExperimentSession

from .models import ResearchSessionMaterialization
from .research_materialization import (
    PROJECTION_VERSION,
    enqueue_session_materialization,
)

_coalescing_materializations = ContextVar('coalescing_research_materializations', default=False)


@contextmanager
def coalesce_session_materializations():
    """Opt in for one domain transaction; registered callbacks track live work."""
    token = _coalescing_materializations.set(True)
    try:
        yield
    finally:
        _coalescing_materializations.reset(token)


def _unscheduled_coalesced_ids(session_ids):
    ids = tuple(dict.fromkeys(session_ids))
    if not _coalescing_materializations.get():
        return ids
    # Django removes a savepoint's callbacks on rollback. A separate remembered
    # set would incorrectly suppress the next dirty write after that rollback.
    pending = {
        session_id
        for _, callback, _ in transaction.get_connection().run_on_commit
        for session_id in getattr(callback, '_coalesced_research_session_ids', ())
    }
    return tuple(session_id for session_id in ids if session_id not in pending)


def _register_materialization_callback(callback, session_ids):
    if _coalescing_materializations.get():
        callback._coalesced_research_session_ids = tuple(session_ids)
    transaction.on_commit(callback, robust=True)


def materialization_enabled_for_course(course_id):
    allowed = settings.ADVANCED_DATA_MATERIALIZED_COURSE_IDS
    return settings.ADVANCED_DATA_MATERIALIZED_ENABLED and (
        "*" in allowed or str(course_id) in allowed
    )


def _enqueue_if_eligible(session_id):
    return _enqueue_eligible_ids((session_id,))


def _mark_dirty_and_enqueue(session_ids):
    ids = _bump_source_revision(session_ids)
    return _enqueue_eligible_ids(ids)


def _bump_source_revision(session_ids):
    ids = tuple(dict.fromkeys(session_ids))
    if not ids:
        return []
    ExperimentSession.objects.filter(pk__in=ids).update(
        research_source_revision=F("research_source_revision") + 1,
        research_source_changed_at=timezone.now(),
    )
    return ids


def _enqueue_eligible_ids(session_ids):
    if not settings.ADVANCED_DATA_MATERIALIZED_ENABLED:
        return []
    sessions = ExperimentSession.objects.filter(
        pk__in=tuple(session_ids),
        status__in=(
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
        ),
    )
    allowed = settings.ADVANCED_DATA_MATERIALIZED_COURSE_IDS
    if "*" not in allowed:
        sessions = sessions.filter(version__experiment__course_id__in=allowed)
    active_session_ids = ResearchSessionMaterialization.objects.filter(
        session_id__in=sessions.values("pk"),
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        ),
    ).values_list("session_id", flat=True)
    failed = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"), source_revision=OuterRef("research_source_revision"),
        projection_version=PROJECTION_VERSION, status=ResearchSessionMaterialization.Status.FAILED,
    )
    eligible = sessions.annotate(failed_revision=Exists(failed)).filter(failed_revision=False).exclude(pk__in=active_session_ids).values_list(
        "pk", "research_source_revision"
    )
    return ResearchSessionMaterialization.objects.bulk_create(
        [
            ResearchSessionMaterialization(
                session_id=session_id,
                source_revision=source_revision,
                projection_version=PROJECTION_VERSION,
            )
            for session_id, source_revision in eligible
        ],
        ignore_conflicts=True,
    )


def schedule_session_materialization(session_id):
    """Queue after the surrounding domain transaction commits."""

    ids = _unscheduled_coalesced_ids((session_id,))
    if ids:
        _bump_source_revision(ids)
        _register_materialization_callback(lambda: _enqueue_if_eligible(session_id), ids)


def schedule_session_materializations(session_ids):
    """Queue many terminal sessions with one lightweight post-commit write."""

    ids = _unscheduled_coalesced_ids(session_ids)
    if ids:
        _bump_source_revision(ids)
        _register_materialization_callback(lambda: _enqueue_eligible_ids(ids), ids)


def _enabled_terminal_sessions():
    queryset = ExperimentSession.objects.filter(
        status__in=(
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
        )
    ).select_related("version__experiment__course")
    allowed = settings.ADVANCED_DATA_MATERIALIZED_COURSE_IDS
    if "*" not in allowed:
        queryset = queryset.filter(version__experiment__course_id__in=allowed)
    return queryset


def reconcile_session_materializations(*, limit=50):
    """Use indexed monotonic revisions; never hash source tables while idle."""

    limit = max(1, min(int(limit), 500))
    if not settings.ADVANCED_DATA_MATERIALIZED_ENABLED:
        return {"scanned": 0, "enqueued": 0}
    active = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"),
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        ),
    )
    current = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"),
        is_current=True,
        status=ResearchSessionMaterialization.Status.SUCCEEDED,
    )
    failed = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"), source_revision=OuterRef("research_source_revision"),
        projection_version=PROJECTION_VERSION, status=ResearchSessionMaterialization.Status.FAILED,
    )
    candidates = list(
        _enabled_terminal_sessions()
        .annotate(
            has_active=Exists(active),
            failed_revision=Exists(failed),
            current_source_revision=Subquery(
                current.values("source_revision")[:1]
            ),
            current_projection_version=Subquery(
                current.values("projection_version")[:1]
            ),
        )
        .filter(has_active=False, failed_revision=False)
        .filter(
            Q(current_source_revision__isnull=True)
            | Q(current_source_revision__lt=F("research_source_revision"))
            | ~Q(current_projection_version=PROJECTION_VERSION)
        )
        .order_by("-terminal_at", "pk")[:limit]
    )
    enqueued = 0
    recent_cutoff = timezone.now() - timedelta(minutes=5)
    for session in candidates:
        priority = 0 if (
            session.current_projection_version == PROJECTION_VERSION
            and session.current_source_revision is not None
            or session.terminal_at and session.terminal_at >= recent_cutoff
            or session.research_source_changed_at and session.research_source_changed_at >= recent_cutoff
        ) else 100
        _, created = enqueue_session_materialization(
            session, return_created=True, priority=priority
        )
        enqueued += int(created)
    return {"scanned": len(candidates), "enqueued": enqueued}


def materialization_metrics(*, now=None):
    now = now or timezone.now()
    active = ResearchSessionMaterialization.objects.filter(
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        )
    )
    oldest = active.order_by("created_at").values_list("created_at", flat=True).first()
    recent = list(
        ResearchSessionMaterialization.objects.filter(
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            started_at__isnull=False,
            finished_at__isnull=False,
        )
        .order_by("-finished_at")
        .values_list("started_at", "finished_at")[:100]
    )
    durations = sorted((finished - started).total_seconds() for started, finished in recent)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    return {
        "backlog_count": active.count(),
        "oldest_wait_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0,
        "failed_count": ResearchSessionMaterialization.objects.filter(
            status=ResearchSessionMaterialization.Status.FAILED
        ).count(),
        "duration_p95_seconds": p95,
    }
