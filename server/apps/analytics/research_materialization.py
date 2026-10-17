import hashlib
import json
from datetime import timedelta

from django.contrib.postgres.aggregates import BitXor
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.models import BigIntegerField, Count, Exists, F, Func, Max, OuterRef, Q, Value
from django.db.models.functions import Cast, JSONObject
from django.utils import timezone

from apps.assessment.models import (
    AssessmentEvent,
    Attempt,
    CaptureContractArtifact,
    ClientCaptureCheckpoint,
    ClientTimeSync,
    ConfidenceAssignment,
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    ResponseInputObservation,
    ResponseSnapshot,
    SimulationSnapshot,
    StudentQuestionAssignment,
    SubmissionEvent,
)

from .data_scope import DataScope
from .models import (
    RecordingCoverageAttempt,
    RecordingCoverageRun,
    RecordingCoverageSession,
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionQuality,
    ResearchSessionResponse,
    ResearchSessionVisit,
)
from .quality_reports import build_quality_report
from .research_dataset import read_research_bundle


PROJECTION_VERSION = "research-session-v6"
MAX_RETRIES = 3
MAX_SOURCE_READ_ATTEMPTS = 3


class SourceChanged(RuntimeError):
    pass


def lock_research_activation():
    """Serialize only activation and cursor cutoffs, never projection generation."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [4_819_520_819])


def _marker(queryset, time_field):
    row = (
        queryset.filter(**{f"{time_field}__isnull": False})
        .order_by(f"-{time_field}", "-pk")
        .values_list(time_field, "pk")
        .first()
    )
    return [row[0].isoformat(), row[1]] if row and row[0] is not None else ["", 0]


def _database_revision(queryset, *, fields=None):
    model = queryset.model
    fields = fields or [field.attname for field in model._meta.concrete_fields]
    if connection.vendor != "postgresql":
        primary_key = model._meta.pk.attname
        digests = (hashlib.sha256(b"0"), hashlib.sha256(b"1"))
        count = 0
        for row in queryset.order_by(primary_key).values(*fields).iterator():
            document = json.dumps(
                row,
                cls=DjangoJSONEncoder,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            framed = len(document).to_bytes(8, "big") + document
            for digest in digests:
                digest.update(framed)
            count += 1
        return {
            "count": count,
            "digest_0": digests[0].hexdigest(),
            "digest_1": digests[1].hexdigest(),
        }
    document = Cast(
        JSONObject(**{field: F(field) for field in fields}),
        models.TextField(),
    )

    def row_hash(seed):
        return Func(
            document,
            Value(seed),
            function="hashtextextended",
            output_field=BigIntegerField(),
        )

    summary = queryset.aggregate(
        count=Count(model._meta.pk.attname),
        digest_0=BitXor(row_hash(0), default=0),
        digest_1=BitXor(row_hash(1), default=0),
    )
    return {
        "count": summary["count"],
        "digest_0": str(summary["digest_0"]),
        "digest_1": str(summary["digest_1"]),
    }


def _source_querysets_for_session(session, *, cutoff=None):
    from apps.accounts.models import Course, User
    from apps.experiments.models import (
        ClassExperimentTask,
        Condition,
        ConditionAssignment,
        Experiment,
        ExperimentClassSnapshot,
        ExperimentVersion,
        ExperimentVersionParticipantSnapshot,
        ItemInstance,
        QuestionSnapshot,
        ScheduleRevision,
    )
    attempts = Attempt.objects.filter(session_id=session.pk)
    if cutoff is not None:
        attempts = attempts.filter(
            models.Q(submitted_at__isnull=True) | models.Q(submitted_at__lte=cutoff)
        )
    attempt_ids = attempts.values("pk")
    assignments = StudentQuestionAssignment.objects.filter(session_id=session.pk)
    assignment_snapshot_ids = assignments.values("snapshot_id")
    assignment_instance_ids = assignments.exclude(item_instance_id__isnull=True).values(
        "item_instance_id"
    )
    events = AssessmentEvent.objects.filter(session_id=session.pk)
    visits = QuestionVisitEvent.objects.filter(session_id=session.pk)
    confidence = ConfidenceAssignment.objects.filter(attempt_id__in=attempt_ids)
    simulations = SimulationSnapshot.objects.filter(attempt_id__in=attempt_ids)
    response_snapshots = ResponseSnapshot.objects.filter(attempt_id__in=attempt_ids)
    input_observations = ResponseInputObservation.objects.filter(attempt_id__in=attempt_ids)
    submissions = SubmissionEvent.objects.filter(attempt__session_id=session.pk)
    grades = GradeRecord.objects.filter(submission__attempt__session_id=session.pk)
    time_syncs = ClientTimeSync.objects.filter(session_id=session.pk)
    checkpoints = ClientCaptureCheckpoint.objects.filter(session_id=session.pk)
    if cutoff is not None:
        events = events.filter(server_received_at__lte=cutoff)
        visits = visits.filter(server_received_at__lte=cutoff)
        confidence = confidence.filter(assigned_at__lte=cutoff)
        simulations = simulations.filter(created_at__lte=cutoff)
        response_snapshots = response_snapshots.filter(created_at__lte=cutoff)
        input_observations = input_observations.filter(observed_at__lte=cutoff)
        submissions = submissions.filter(server_received_at__lte=cutoff)
        grades = grades.filter(created_at__lte=cutoff)
        time_syncs = time_syncs.filter(created_at__lte=cutoff)
        checkpoints = checkpoints.filter(server_received_at__lte=cutoff)
    condition_assignments = ConditionAssignment.objects.filter(
        models.Q(session_id=session.pk)
        | models.Q(
            subject_id=session.student_id,
            condition__experiment_id=session.version.experiment_id,
        )
    )
    if cutoff is not None:
        condition_assignments = condition_assignments.filter(assigned_at__lte=cutoff)
    coverage_sessions = RecordingCoverageSession.objects.filter(
        session_id=session.pk,
        run__run_status=RecordingCoverageRun.Status.COMPLETED,
    )
    coverage_attempts = RecordingCoverageAttempt.objects.filter(
        attempt_id__in=attempt_ids,
        run__run_status=RecordingCoverageRun.Status.COMPLETED,
    )
    if cutoff is not None:
        coverage_sessions = coverage_sessions.filter(run__source_cutoff_at__lte=cutoff)
        coverage_attempts = coverage_attempts.filter(run__source_cutoff_at__lte=cutoff)
    coverage_run_ids = coverage_sessions.values("run_id").union(
        coverage_attempts.values("run_id")
    )

    course_id = session.version.experiment.course_id
    class_snapshots = ExperimentClassSnapshot.objects.filter(pk=session.class_snapshot_id)
    participant_snapshots = ExperimentVersionParticipantSnapshot.objects.filter(
        pk=session.participant_snapshot_id
    )
    if cutoff is not None:
        class_snapshots = class_snapshots.filter(created_at__lte=cutoff)
        participant_snapshots = participant_snapshots.filter(created_at__lte=cutoff)

    return {
        "session": ExperimentSession.objects.filter(pk=session.pk),
        "identity": User.objects.filter(pk=session.student_id),
        "course": Course.objects.filter(pk=course_id),
        "experiment": Experiment.objects.filter(pk=session.version.experiment_id),
        "version": ExperimentVersion.objects.filter(pk=session.version_id),
        "class_task": ClassExperimentTask.objects.filter(pk=session.class_task_id),
        "participant_snapshot": participant_snapshots,
        "schedule_revision": ScheduleRevision.objects.filter(pk=session.schedule_revision_id),
        "class_snapshot": class_snapshots,
        "capture_contract": CaptureContractArtifact.objects.filter(
            pk=session.capture_contract_id
        ),
        "assignments": assignments,
        "question_snapshots": QuestionSnapshot.objects.filter(
            pk__in=assignment_snapshot_ids
        ),
        "item_instances": ItemInstance.objects.filter(pk__in=assignment_instance_ids),
        "attempts": attempts,
        "visits": visits,
        "events": events,
        "confidence": confidence,
        "simulations": simulations,
        "response_snapshots": response_snapshots,
        "input_observations": input_observations,
        "submissions": submissions,
        "grades": grades,
        "time_syncs": time_syncs,
        "checkpoints": checkpoints,
        "conditions": Condition.objects.filter(experiment_id=session.version.experiment_id),
        "condition_assignments": condition_assignments,
        "coverage_sessions": coverage_sessions,
        "coverage_attempts": coverage_attempts,
        "coverage_runs": RecordingCoverageRun.objects.filter(pk__in=coverage_run_ids),
    }


def source_watermarks_for_session(session, *, cutoff=None):
    session = ExperimentSession.objects.select_related("version__experiment").get(
        pk=session.pk
    )
    querysets = _source_querysets_for_session(session, cutoff=cutoff)
    identity_fields = (
        "pk", "username", "first_name", "last_name", "display_name", "student_number"
    )
    sources = {
        name: _database_revision(
            queryset,
            fields=identity_fields if name == "identity" else None,
        )
        for name, queryset in querysets.items()
    }
    serialized = json.dumps(
        sources,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "revision": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "sources": sources,
        "event": querysets["events"].aggregate(
            value=Max("server_ingest_sequence_no")
        )["value"] or 0,
        "grade": {
            "submission": _marker(querysets["submissions"], "committed_at"),
            "grade": _marker(querysets["grades"], "created_at"),
            "voided_grade": _marker(
                querysets["grades"].filter(voided_at__isnull=False), "voided_at"
            ),
        },
    }


def _scope_for_session(session):
    values = {
        "experiment_id": session.version.experiment_id,
        "version_id": session.version_id,
        "session_id": session.pk,
    }
    if session.class_snapshot_id:
        values["class_snapshot_id"] = str(session.class_snapshot_id)
    return DataScope.from_mapping(session.version.experiment.course, values)


def _read_consistent_bundle(session):
    from .recording_coverage import generate_session_coverage

    scope = _scope_for_session(session)
    for _attempt in range(MAX_SOURCE_READ_ATTEMPTS):
        before = _revision_marker(session.pk)
        coverage_run = generate_session_coverage(session)
        coverage = coverage_run.session_results.get(session_id=session.pk)
        input_status = coverage.overall_status
        attempts = coverage_run.attempt_results.annotate(
            has_client_evidence=Exists(AssessmentEvent.objects.filter(
                attempt_id=OuterRef("attempt_id"), actor_source="client",
            )),
            has_visit=Exists(QuestionVisitEvent.objects.filter(attempt_id=OuterRef("attempt_id"))),
        )
        required = (
            Q(attempt__visit_count__gt=0) | Q(attempt__first_opened_at__isnull=False)
            | Q(attempt__last_opened_at__isnull=False) | Q(has_visit=True) | Q(has_client_evidence=True)
            | Q(attempt__active_response_event__isnull=False) | Q(attempt__final_response_event__isnull=False)
            | Q(attempt__current_response_snapshot__isnull=False) | Q(attempt__final_response_snapshot__isnull=False)
            | Q(attempt__response_version__gt=0) | Q(attempt__draft_saved_at__isnull=False)
            | Q(attempt__submission_event__isnull=False) | Q(attempt__submitted_at__isnull=False)
            | Q(attempt__status__in=(Attempt.Status.VISITED, Attempt.Status.DRAFT, Attempt.Status.SUBMITTED))
        )
        attempt_counts = attempts.aggregate(
            total=Count("pk"), required=Count("pk", filter=required),
            incomplete=Count("pk", filter=required & ~Q(overall_status__in=("complete", "not_applicable"))),
        )
        if input_status == "complete" and attempt_counts["incomplete"]:
            input_status = "partial"
        bundle, cutoff = read_research_bundle(
            scope,
            include_course_catalog=False,
        )
        after = _revision_marker(session.pk)
        if before == after:
            for summary in bundle.get("sessions", []):
                summary["input_completeness"] = {
                    "status": input_status,
                    "coverage_run_id": str(coverage_run.pk),
                    "required_attempt_count": attempt_counts["required"],
                    "unobserved_attempt_count": attempt_counts["total"] - attempt_counts["required"],
                }
            return bundle, cutoff, after
    raise RuntimeError("research source changed during materialization")


def _revision_marker(session_id):
    values = ExperimentSession.objects.filter(pk=session_id).values(
        "research_source_revision", "next_ingest_sequence_no", "research_source_changed_at"
    ).get()
    return {
        "revision": values["research_source_revision"],
        "changed_at": values["research_source_changed_at"].isoformat()
        if values["research_source_changed_at"] else None,
        "sources": {},
        "event": max(0, values["next_ingest_sequence_no"] - 1),
        "grade": {},
    }


@transaction.atomic
def enqueue_session_materialization(
    session, *, source_watermarks=None, return_created=False, force_retry=False, priority=0
):
    ExperimentSession.objects.select_for_update().only("pk").get(pk=session.pk)
    session = ExperimentSession.objects.select_related(
        "version__experiment__course"
    ).get(pk=session.pk)
    if session.status not in (ExperimentSession.Status.COMPLETED, ExperimentSession.Status.EXPIRED):
        raise ValueError("research materialization requires a completed or expired session")
    active = session.research_materializations.filter(
        status__in=(
            ResearchSessionMaterialization.Status.QUEUED,
            ResearchSessionMaterialization.Status.RUNNING,
        )
    ).first()
    if active:
        return (active, False) if return_created else active
    failed = session.research_materializations.filter(
        status=ResearchSessionMaterialization.Status.FAILED,
        source_revision=session.research_source_revision,
        projection_version=PROJECTION_VERSION,
    ).order_by("-created_at").first()
    if failed and not force_retry:
        return (failed, False) if return_created else failed
    marks = source_watermarks or _revision_marker(session.pk)
    item = ResearchSessionMaterialization.objects.create(
        session=session,
        source_revision=session.research_source_revision,
        source_cutoff_at=timezone.now(),
        event_high_watermark=marks["event"],
        grade_high_watermark=marks["grade"],
        source_high_watermarks=marks,
        projection_version=PROJECTION_VERSION,
        priority=priority,
    )
    return (item, True) if return_created else item


@transaction.atomic
def claim_session_materialization(*, worker_id, now=None, lease_seconds=90):
    from .runtime_pressure import runtime_pressure

    mode = runtime_pressure()['mode']
    priority_filter = models.Q(priority=0) if mode in ('capture_priority', 'capture_emergency') else models.Q()
    now = now or timezone.now()
    while True:
        item = (
            ResearchSessionMaterialization.objects.select_for_update(skip_locked=True)
            .select_related("session")
            .filter(priority_filter)
            .filter(
                (models.Q(status=ResearchSessionMaterialization.Status.QUEUED)
                 & (models.Q(retry_count=0) | models.Q(next_attempt_at__lte=now)))
                | models.Q(
                    status=ResearchSessionMaterialization.Status.RUNNING,
                    lease_expires_at__lte=now,
                )
            )
            .order_by("priority", "created_at", "materialization_id")
            .first()
        )
        if item is None:
            return None
        if item.projection_version != PROJECTION_VERSION:
            item.status = item.Status.FAILED
            item.locked_by = ""
            item.lease_expires_at = None
            item.heartbeat_at = now
            item.finished_at = now
            item.error = "research projection version superseded; historical job retained"
            item.save(update_fields=[
                "status", "locked_by", "lease_expires_at", "heartbeat_at",
                "finished_at", "error",
            ])
            continue
        if item.session.status not in (
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
        ):
            _mark_invalidated_locked(item, now=now)
            continue
        if item.status == ResearchSessionMaterialization.Status.RUNNING:
            item.retry_count += 1
            if item.retry_count >= MAX_RETRIES:
                item.status = item.Status.FAILED
                item.locked_by = ""
                item.lease_expires_at = None
                item.heartbeat_at = now
                item.finished_at = now
                item.error = "research materialization lease expired at retry limit"
                item.save(update_fields=[
                    "retry_count", "status", "locked_by", "lease_expires_at",
                    "heartbeat_at", "finished_at", "error",
                ])
                continue
        item.status = ResearchSessionMaterialization.Status.RUNNING
        item.source_revision = item.session.research_source_revision
        item.locked_by = worker_id
        item.heartbeat_at = now
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.started_at = item.started_at or now
        item.error = ""
        item.save(update_fields=[
            "status", "locked_by", "heartbeat_at", "lease_expires_at",
            "started_at", "retry_count", "error", "source_revision",
        ])
        return item


@transaction.atomic
def renew_session_materialization_lease(item, *, worker_id, now=None, lease_seconds=90):
    now = now or timezone.now()
    current = ResearchSessionMaterialization.objects.select_for_update().get(pk=item.pk)
    if (
        current.status != current.Status.RUNNING
        or current.locked_by != worker_id
        or current.lease_expires_at is None
        or current.lease_expires_at <= now
    ):
        return None
    current.heartbeat_at = now
    current.lease_expires_at = now + timedelta(seconds=lease_seconds)
    current.save(update_fields=["heartbeat_at", "lease_expires_at"])
    return current


def _payload_without_row(item):
    return {key: value for key, value in item.items() if key != "row"}


def _quality_rows_for_session(session, bundle):
    question_refs = {
        int(row["assigned_order"]): row.get("question_ref", "")
        for row in bundle.get("responses", [])
        if row.get("assigned_order") is not None
    }
    attempt_orders = dict(
        Attempt.objects.filter(session_id=session.pk).values_list(
            "pk", "assignment__assigned_sequence_index"
        )
    )
    report = build_quality_report(_scope_for_session(session))
    return [
        {
            "attempt_id": row.attempt_id,
            "question_code": question_refs.get(attempt_orders.get(row.attempt_id), ""),
            "rule": row.rule,
            "state": str(row.state),
            "detail": row.detail,
            "rule_version": row.rule_version,
            "entity_type": row.entity_type,
            "reason_code": row.reason_code,
            "source_mode": row.source_mode,
        }
        for row in report.rows
    ]


def _mark_invalidated_locked(item, *, now):
    ResearchSessionMaterialization.objects.filter(
        session_id=item.session_id, is_current=True
    ).update(is_current=False)
    item.status = item.Status.FAILED
    item.is_current = False
    item.locked_by = ""
    item.lease_expires_at = None
    item.heartbeat_at = now
    item.finished_at = now
    item.error = "research materialization session was invalidated"
    item.save(update_fields=[
        "status", "is_current", "locked_by", "lease_expires_at",
        "heartbeat_at", "finished_at", "error",
    ])
    return item


@transaction.atomic
def _mark_invalidated(item_id, *, now=None):
    now = now or timezone.now()
    item = ResearchSessionMaterialization.objects.select_for_update().get(pk=item_id)
    ExperimentSession.objects.select_for_update().only("pk").get(pk=item.session_id)
    return _mark_invalidated_locked(item, now=now)


def _lock_session_sources(session_id):
    return ExperimentSession.objects.select_for_update(of=("self",)).select_related(
        "version__experiment__course", "class_snapshot"
    ).get(pk=session_id)


@transaction.atomic
def _store_success(
    item_id, *, worker_id, bundle, cutoff, watermarks, quality_rows=(), now=None
):
    requested_now = now
    lease_checked_at = now or timezone.now()
    item = ResearchSessionMaterialization.objects.select_for_update().get(pk=item_id)
    session = _lock_session_sources(item.session_id)
    if session.status == ExperimentSession.Status.INVALIDATED:
        raise ValueError("research materialization session was invalidated")
    if session.status not in (
        ExperimentSession.Status.COMPLETED,
        ExperimentSession.Status.EXPIRED,
    ):
        raise ValueError("research materialization session is not terminal")
    if (
        item.status != item.Status.RUNNING
        or item.locked_by != worker_id
        or item.lease_expires_at is None
        or item.lease_expires_at <= lease_checked_at
    ):
        raise ValueError("research materialization lease is not owned by this worker")
    if session.research_source_revision != item.source_revision:
        raise SourceChanged("research source changed before materialization publish")
    sessions = bundle.get("sessions", [])
    session_rows = bundle.get("tables", {}).get("sessions", ((), []))[1]
    if len(sessions) != 1 or len(session_rows) != 1:
        raise ValueError("per-session research projection must contain exactly one session")
    responses = bundle.get("responses", [])
    response_rows = bundle.get("files", {}).get("responses", [])
    if len(responses) != len(response_rows):
        raise ValueError("research response projection rows are inconsistent")
    ResearchSessionResponse.objects.bulk_create([
        ResearchSessionResponse(
            materialization=item,
            ordinal=ordinal,
            question_ref=row.get("question_ref", ""),
            assigned_order=row.get("assigned_order"),
            first_visit_order=row.get("first_visit_order"),
            row=response_rows[ordinal - 1],
            payload=row,
        )
        for ordinal, row in enumerate(responses, 1)
    ])
    visits = bundle.get("visits", [])
    ResearchSessionVisit.objects.bulk_create([
        ResearchSessionVisit(
            materialization=item,
            ordinal=ordinal,
            question_ref=row.get("question_ref", ""),
            visit_key=row.get("key", ""),
            visit_index=row.get("visit_index"),
            entered_at=row.get("entered_at", ""),
            row=row.get("row", []),
            payload=_payload_without_row(row),
        )
        for ordinal, row in enumerate(visits, 1)
    ])
    behavior = bundle.get("behavior", [])
    ResearchSessionBehavior.objects.bulk_create([
        ResearchSessionBehavior(
            materialization=item,
            ordinal=ordinal,
            question_ref=row.get("question_ref", ""),
            visit_key=row.get("visit_key") or "",
            sequence=row.get("sequence"),
            category=row.get("category", ""),
            action=row.get("action", ""),
            occurred_at=row.get("occurred_at", ""),
            row=row.get("row", []),
            payload=_payload_without_row(row),
        )
        for ordinal, row in enumerate(behavior, 1)
    ])
    ResearchSessionQuality.objects.bulk_create([
        ResearchSessionQuality(
            materialization=item, ordinal=ordinal,
            attempt_id=row.get("attempt_id"), question_ref=row.get("question_code", ""),
            rule=row.get("rule", ""), state=row.get("state", ""), payload=row,
        )
        for ordinal, row in enumerate(quality_rows, 1)
    ])
    lock_research_activation()
    previous = ResearchSessionMaterialization.objects.select_for_update().filter(
        session_id=item.session_id, is_current=True
    ).exclude(pk=item.pk)
    list(previous.values_list("pk", flat=True))
    published_at = requested_now or timezone.now()
    if item.lease_expires_at <= published_at:
        raise ValueError("research materialization lease expired before publish")
    previous.update(is_current=False, superseded_at=published_at)
    item.status = item.Status.SUCCEEDED
    item.is_current = True
    item.source_cutoff_at = cutoff
    item.event_high_watermark = watermarks["event"]
    item.grade_high_watermark = watermarks["grade"]
    item.source_high_watermarks = watermarks
    item.source_revision = session.research_source_revision
    item.projection_version = PROJECTION_VERSION
    item.locked_by = ""
    item.lease_expires_at = None
    item.heartbeat_at = published_at
    item.finished_at = published_at
    item.activated_at = published_at
    item.superseded_at = None
    item.error = ""
    item.session_summary = sessions[0]
    item.session_row = session_rows[0]
    item.quality_rows = list(quality_rows)
    item.row_counts = {
        "responses": len(responses), "visits": len(visits), "behavior": len(behavior)
    }
    item.save()
    return item


@transaction.atomic
def _store_failure(item_id, *, worker_id, error):
    item = ResearchSessionMaterialization.objects.select_for_update().get(pk=item_id)
    if (
        item.status != item.Status.RUNNING
        or item.locked_by != worker_id
        or item.lease_expires_at is None
        or item.lease_expires_at <= timezone.now()
    ):
        return item
    item.retry_count += 1
    from .services import ExportContractError
    deterministic = isinstance(error, (ExportContractError, ValueError, TypeError))
    item.status = item.Status.FAILED if deterministic or item.retry_count >= MAX_RETRIES else item.Status.QUEUED
    item.next_attempt_at = timezone.now() + timedelta(seconds=5 * 2 ** (item.retry_count - 1))
    item.locked_by = ""
    item.lease_expires_at = None
    item.heartbeat_at = timezone.now()
    item.finished_at = timezone.now() if item.status == item.Status.FAILED else None
    item.error = str(error)
    item.save(update_fields=[
        "retry_count", "status", "locked_by", "lease_expires_at",
        "heartbeat_at", "finished_at", "error", "next_attempt_at",
    ])
    return item


def run_session_materialization(item, *, worker_id):
    try:
        current = ResearchSessionMaterialization.objects.select_related(
            "session__version__experiment__course", "session__class_snapshot"
        ).get(pk=item.pk)
        if current.session.status == ExperimentSession.Status.INVALIDATED:
            return _mark_invalidated(current.pk)
        if current.status != current.Status.RUNNING or current.locked_by != worker_id:
            raise ValueError("research materialization lease is not owned by this worker")
        for _attempt in range(MAX_SOURCE_READ_ATTEMPTS):
            bundle, cutoff, watermarks = _read_consistent_bundle(current.session)
            ResearchSessionMaterialization.objects.filter(
                pk=current.pk, status=ResearchSessionMaterialization.Status.RUNNING,
                locked_by=worker_id,
            ).update(source_revision=watermarks["revision"])
            quality_rows = _quality_rows_for_session(current.session, bundle)
            try:
                return _store_success(
                    current.pk, worker_id=worker_id, bundle=bundle,
                    cutoff=cutoff, watermarks=watermarks, quality_rows=quality_rows,
                )
            except SourceChanged:
                latest_revision = ExperimentSession.objects.values_list(
                    "research_source_revision", flat=True
                ).get(pk=current.session_id)
                ResearchSessionMaterialization.objects.filter(
                    pk=current.pk,
                    status=ResearchSessionMaterialization.Status.RUNNING,
                    locked_by=worker_id,
                ).update(source_revision=latest_revision)
                current.source_revision = latest_revision
                continue
        raise RuntimeError("research source changed before materialization publish")
    except Exception as error:
        return _store_failure(item.pk, worker_id=worker_id, error=error)


def materialization_is_stale(item):
    if ExperimentSession.objects.filter(
        pk=item.session_id, status=ExperimentSession.Status.INVALIDATED
    ).exists():
        return True
    if item.projection_version != PROJECTION_VERSION:
        return True
    return ExperimentSession.objects.filter(
        pk=item.session_id,
        research_source_revision__gt=item.source_revision,
    ).exists()
