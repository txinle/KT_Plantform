"""Scheduling and operational metrics for session research projections."""

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from apps.assessment.models import ExperimentSession

from .models import ResearchSessionMaterialization
from .research_materialization import (
    PROJECTION_VERSION,
    enqueue_session_materialization,
)


def materialization_enabled_for_course(course_id):
    allowed = settings.ADVANCED_DATA_MATERIALIZED_COURSE_IDS
    return settings.ADVANCED_DATA_MATERIALIZED_ENABLED and (
        "*" in allowed or str(course_id) in allowed
    )


def _enqueue_if_eligible(session_id):
    return _enqueue_eligible_ids((session_id,))


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
    eligible_ids = sessions.exclude(pk__in=active_session_ids).values_list("pk", flat=True)
    return ResearchSessionMaterialization.objects.bulk_create(
        [
            ResearchSessionMaterialization(
                session_id=session_id,
                projection_version=PROJECTION_VERSION,
            )
            for session_id in eligible_ids
        ],
        ignore_conflicts=True,
    )


def schedule_session_materialization(session_id):
    """Queue after the surrounding domain transaction commits."""

    transaction.on_commit(lambda: _enqueue_if_eligible(session_id), robust=True)


def schedule_session_materializations(session_ids):
    """Queue many terminal sessions with one lightweight post-commit write."""

    ids = tuple(dict.fromkeys(session_ids))
    if ids:
        transaction.on_commit(lambda: _enqueue_eligible_ids(ids), robust=True)


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
    """Bound each cycle while eventually checking every complete source fingerprint."""

    limit = max(1, min(int(limit), 500))
    if not settings.ADVANCED_DATA_MATERIALIZED_ENABLED:
        return {"scanned": 0, "enqueued": 0}
    scanned = 0
    enqueued = 0
    active = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"),
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        ),
    )
    current = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"), is_current=True
    )
    missing = list(
        _enabled_terminal_sessions()
        .annotate(has_active=Exists(active), has_current=Exists(current))
        .filter(has_active=False, has_current=False)
        .order_by("terminal_at", "pk")[:limit]
    )
    empty_marks = {"revision": "", "sources": {}, "event": 0, "grade": {}}
    for session in missing:
        scanned += 1
        _, created = enqueue_session_materialization(
            session, source_watermarks=empty_marks, return_created=True
        )
        enqueued += int(created)

    remaining = limit - scanned
    if remaining <= 0:
        return {"scanned": scanned, "enqueued": enqueued}

    active_for_current = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("session_id"),
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        ),
    )
    current_items = ResearchSessionMaterialization.objects.filter(
        is_current=True,
        status=ResearchSessionMaterialization.Status.SUCCEEDED,
        session__in=_enabled_terminal_sessions(),
    ).annotate(has_active=Exists(active_for_current)).filter(has_active=False)
    current_items = list(
        current_items.select_related("session__version__experiment__course")
        .order_by(
            F("last_reconciled_at").asc(nulls_first=True),
            "finished_at",
            "materialization_id",
        )[:remaining]
    )
    reconciled_at = timezone.now()
    for item in current_items:
        scanned += 1
        marks = None
        stale = item.projection_version != PROJECTION_VERSION
        if not stale:
            from .research_materialization import source_watermarks_for_session

            marks = source_watermarks_for_session(item.session)
            stale = marks != item.source_high_watermarks
        ResearchSessionMaterialization.objects.filter(
            pk=item.pk, is_current=True
        ).update(last_reconciled_at=reconciled_at)
        if stale:
            _, created = enqueue_session_materialization(
                item.session,
                source_watermarks=marks or empty_marks,
                return_created=True,
            )
            enqueued += int(created)
    return {"scanned": scanned, "enqueued": enqueued}


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
