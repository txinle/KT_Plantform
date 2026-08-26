import hashlib
import json
import csv
import io
import os
import subprocess
import zipfile
from datetime import timedelta

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from apps.assessment.models import (
    AssessmentEvent, Attempt, ExperimentSession, EventSchemaArtifact,
    StudentQuestionAssignment, ConfidenceAssignment, SimulationSnapshot,
)
from apps.experiments.models import ItemInstance, QuestionSnapshot
from .export_catalog import EXPOSURE_EVENT_TYPES, PACKAGE_TABLES, RESPONSE_EVENT_TYPES, SIMULATION_EVENT_TYPES, TABLE_PATHS

from .models import ExportJob, ExportSnapshot


def create_export_snapshot(*, course=None, experiment=None, research_study=None, created_by):
    versions = []
    if experiment is not None:
        versions = list(experiment.versions.order_by("version_number").values_list("pk", "version_number"))
    return ExportSnapshot.objects.create(
        course=course,
        experiment=experiment,
        research_study=research_study,
        created_by=created_by,
        cutoff_at=timezone.now(),
        selected_versions=[{"id": pk, "version": version} for pk, version in versions],
    )


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_export_manifest(snapshot, *, selected_tables, row_counts=None):
    schemas = {
        f"{row.event_type}:v{row.event_version}": row.schema_sha256
        for row in EventSchemaArtifact.objects.all()
    }
    config = {"selected_tables": list(selected_tables), "selected_versions": snapshot.selected_versions}
    return {
        "manifest_version": 1,
        "export_snapshot_id": str(snapshot.snapshot_id),
        "course_id": snapshot.course_id,
        "experiment_id": snapshot.experiment_id,
        "research_study_id": snapshot.research_study_id,
        "cutoff_at": snapshot.cutoff_at.isoformat(),
        "source_cutoff_at": snapshot.cutoff_at.isoformat(),
        "database_snapshot_strategy": snapshot.database_snapshot_strategy,
        "selected_tables": list(selected_tables),
        "event_schema_hashes": schemas,
        "application_git_commit_sha": _git_commit(),
        "export_config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "row_counts": row_counts or {},
    }


def manifest_bytes(manifest):
    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def claim_export_job(*, worker_id, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        job = (
            ExportJob.objects.select_for_update()
            .filter(
                models.Q(status=ExportJob.Status.QUEUED)
                | models.Q(status=ExportJob.Status.RUNNING, lease_expires_at__lt=now)
            )
            .order_by("created_at")
            .first()
        )
        if not job:
            return None
        job.status = ExportJob.Status.RUNNING
        job.locked_by = worker_id
        job.started_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=90)
        job.progress_stage = "snapshot"
        job.save(update_fields=["status", "locked_by", "started_at", "heartbeat_at", "lease_expires_at", "progress_stage"])
        return job


def renew_export_job_lease(job, *, worker_id, now=None):
    now = now or timezone.now()
    updated = ExportJob.objects.filter(
        pk=job.pk,
        status=ExportJob.Status.RUNNING,
        locked_by=worker_id,
    ).update(
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=90),
    )
    if not updated:
        return None
    job.refresh_from_db(fields=["heartbeat_at", "lease_expires_at"])
    return job


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def run_export_job(job):
    """Build a deterministic, pseudonymized ZIP from the job's frozen cutoff."""
    snapshot = job.snapshot
    root = os.path.join(settings.MEDIA_ROOT, "exports", str(job.job_id))
    os.makedirs(root, exist_ok=True)
    zip_path = os.path.join(root, f"{job.package}-{job.job_id}.zip")
    study_course_ids = list(job.research_study.course_links.values_list("course_id", flat=True)) if job.research_study_id else []
    experiment_filter = {"session__version__experiment_id": job.experiment_id} if job.experiment_id else ({"session__version__experiment__course_id__in": study_course_ids} if study_course_ids else {"session__version__experiment__course_id": job.course_id})
    session_filter = {"version__experiment_id": job.experiment_id} if job.experiment_id else ({"version__experiment__course_id__in": study_course_ids} if study_course_ids else {"version__experiment__course_id": job.course_id})
    with transaction.atomic():
        if connection_vendor := connection.vendor:
            if connection_vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        sessions = list(ExperimentSession.objects.filter(**session_filter, started_at__lte=snapshot.cutoff_at).select_related("student", "version"))
        session_ids = [s.pk for s in sessions]
        attempts = list(Attempt.objects.filter(**experiment_filter, session__started_at__lte=snapshot.cutoff_at).filter(models.Q(submitted_at__isnull=True) | models.Q(submitted_at__lte=snapshot.cutoff_at)).select_related("session", "assignment__snapshot", "assignment__item_instance"))
        events = list(AssessmentEvent.objects.filter(**({"session__in": session_ids}), server_received_at__lte=snapshot.cutoff_at).order_by("session_id", "server_ingest_sequence_no"))
        assignments = list(StudentQuestionAssignment.objects.filter(session_id__in=session_ids).select_related("snapshot", "item_instance"))
        confidence_assignments = list(ConfidenceAssignment.objects.filter(attempt_id__in=[a.pk for a in attempts]))
        simulations = list(SimulationSnapshot.objects.filter(attempt_id__in=[a.pk for a in attempts]))
    if job.kind == ExportJob.Kind.IDENTITY:
        selected_tables = ["identity_mapping"]
    elif job.selected_tables:
        selected_tables = list(job.selected_tables)
    else:
        selected_tables = list(PACKAGE_TABLES.get(job.package, PACKAGE_TABLES["complete"]))
    rows = _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations)
    row_counts = {table: len(rows.get(table, ())) for table in selected_tables if table in rows}
    manifest = build_export_manifest(snapshot, selected_tables=selected_tables, row_counts=row_counts)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("00_manifest/manifest.json", manifest_bytes(manifest))
        archive.writestr("01_design_metadata/README.txt", "All identifiers in this package are pseudonymized. Identity linkage is managed separately.\n")
        if job.kind == ExportJob.Kind.IDENTITY:
            students = list(job.experiment.students.select_related("class_group")) if job.experiment_id else []
            archive.writestr(
                "01_identity/identity_mapping.csv",
                _csv_bytes(
                    ["pseudonymized_student_id", "student_number", "student_name", "class_name"],
                    [[_anon_subject(s.id, job.course_id), s.student_number, s.shown_name, str(s.class_group or "")] for s in students],
                ),
            )
            return _finalize_export_job(job, zip_path, manifest)
        for table in selected_tables:
            if table == "identity_mapping" or table not in rows:
                continue
            path = TABLE_PATHS.get(table)
            if path:
                headers, values = rows[table]
                archive.writestr(path, _csv_bytes(headers, values))
    return _finalize_export_job(job, zip_path, manifest)


def _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations):
    subject_values = [[_subject_uuid(s.student_id, job.course_id, job.research_study_id)] for s in sessions]
    attempt_ids = {a.pk for a in attempts}
    event_rows = [[e.event_id, e.event_type, e.event_version, e.session_id, e.attempt_id, e.client_instance_id, e.client_sequence_no, e.server_ingest_sequence_no, e.correlation_id, e.server_received_at.isoformat(), e.capture_mode, json.dumps(e.quality_flags, ensure_ascii=False), json.dumps(e.payload, ensure_ascii=False)] for e in events]
    response_rows = [row for row, event in zip(event_rows, events) if event.event_type in RESPONSE_EVENT_TYPES]
    exposure_rows = [row for row, event in zip(event_rows, events) if event.event_type in EXPOSURE_EVENT_TYPES]
    telemetry_rows = [row for row, event in zip(event_rows, events) if event.event_domain == AssessmentEvent.EventDomain.TELEMETRY]
    simulation_event_rows = [row for row, event in zip(event_rows, events) if event.event_type in SIMULATION_EVENT_TYPES]
    subject_header = "study_subject_id" if job.research_study_id else "course_subject_id"
    rows = {
        "subjects": ((subject_header,), subject_values),
        "sessions": (("session_id", "subject_id", "experiment_id", "version", "status", "started_at", "completed_at"), [[s.pk, _subject_uuid(s.student_id, job.course_id, job.research_study_id), s.version.experiment_id, s.version.version_number, s.status, s.started_at.isoformat(), s.completed_at.isoformat() if s.completed_at else ""] for s in sessions]),
        "assignments": (("assignment_id", "session_id", "question_snapshot_id", "item_instance_id", "assigned_sequence_index"), [[a.pk, a.session_id, a.snapshot_id, a.item_instance_id, a.assigned_sequence_index] for a in assignments]),
        "item_instances": (("item_instance_id", "question_snapshot_id", "presentation_variant", "option_order", "parameter_values", "random_seed", "presentation_hash"), [[a.item_instance_id, a.snapshot_id, a.item_instance.presentation_variant, json.dumps(a.item_instance.option_order), json.dumps(a.item_instance.parameter_values), a.item_instance.random_seed, a.item_instance.presentation_hash] for a in assignments]),
        "attempts": (("attempt_id", "session_id", "subject_id", "question_code", "status", "is_correct", "score", "submitted_at"), [[a.pk, a.session_id, _subject_uuid(a.session.student_id, job.course_id, job.research_study_id), a.assignment.snapshot.code, a.status, a.is_correct, a.score, a.submitted_at.isoformat() if a.submitted_at else ""] for a in attempts]),
        "assessment_events": (("event_id", "event_type", "event_version", "session_id", "attempt_id", "client_instance_id", "client_sequence_no", "server_ingest_sequence_no", "correlation_id", "server_received_at", "capture_mode", "quality_flags", "payload"), event_rows),
        "response_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in response_rows]),
        "exposure_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in exposure_rows]),
        "navigation_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in events if r[1] in ("item_entered", "item_left")]),
        "telemetry_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in telemetry_rows]),
        "simulation_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in simulation_event_rows]),
        "simulation_snapshots": (("snapshot_id", "attempt_id", "simulation_version", "state_metadata", "content_hash", "content_size", "created_at"), [[s.snapshot_id, s.attempt_id, s.simulation_version, json.dumps(s.state_metadata), s.content_hash, s.content_size, s.created_at.isoformat()] for s in simulations]),
        "confidence_assignments": (("confidence_assignment_id", "attempt_id", "condition", "assignment_unit", "assignment_version", "response_status", "probability", "assigned_at", "committed_at"), [[c.confidence_assignment_id, c.attempt_id, c.condition, c.assignment_unit, c.assignment_version, c.response_status, c.probability, c.assigned_at.isoformat(), c.committed_at.isoformat() if c.committed_at else ""] for c in confidence_assignments]),
        "item_snapshots": (("question_snapshot_id", "version_id", "code", "question_type", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.version_id, a.snapshot.code, a.snapshot.question_type, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in assignments]),
        "item_kc_mapping": (("question_snapshot_id", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in assignments]),
        "q_matrix": (("question_snapshot_id", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in assignments]),
    }
    rows.update({k: (("attempt_id",), [[a.pk] for a in attempts]) for k in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features")})
    return rows


def _anon_subject(student_id, course_id):
    return hashlib.sha256(f"kt-course:{course_id}:{student_id}".encode()).hexdigest()[:16]


def _finalize_export_job(job, zip_path, manifest):
    digest = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    job.status = ExportJob.Status.SUCCEEDED
    job.finished_at = timezone.now()
    job.progress_stage = "complete"
    job.artifact_path = zip_path
    job.artifact_sha256 = digest
    job.save(update_fields=["status", "finished_at", "progress_stage", "artifact_path", "artifact_sha256"])
    return job


def _subject_uuid(student_id, course_id, study_id=None):
    from apps.accounts.models import CourseSubject, ResearchStudyParticipant
    if study_id:
        participant = ResearchStudyParticipant.objects.filter(student_id=student_id, study_id=study_id).first()
        if participant:
            return str(participant.public_uuid)
    subject = CourseSubject.objects.filter(student_id=student_id, course_id=course_id).first()
    return str(subject.public_uuid) if subject else ""


def execute_purge_request(purge_request, *, executed_by):
    from apps.accounts.models import DataPurgeRequest
    from apps.assessment.models import (
        AssessmentEvent, Attempt, ClientTimeSync, ConfidenceAssignment,
        DraftSaveEvent, ExperimentSession, QuestionVisitEvent, ResponseSnapshot,
        SimulationSnapshot, StudentQuestionAssignment, SubmissionEvent,
    )
    course_id = purge_request.scope_definition.get("course_id")
    if not course_id:
        raise ValueError("purge scope missing course_id")
    with transaction.atomic():
        session_ids = list(ExperimentSession.objects.filter(version__experiment__course_id=course_id).values_list("pk", flat=True))
        attempt_ids = list(Attempt.objects.filter(session_id__in=session_ids).values_list("pk", flat=True))
        counts = {"sessions": len(session_ids), "attempts": len(attempt_ids), "events": AssessmentEvent.objects.filter(session_id__in=session_ids).count()}
        for model, field, ids in (
            (ConfidenceAssignment, "attempt_id", attempt_ids),
            (ResponseSnapshot, "attempt_id", attempt_ids),
            (SimulationSnapshot, "attempt_id", attempt_ids),
            (SubmissionEvent, "attempt_id", attempt_ids),
            (DraftSaveEvent, "attempt_id", attempt_ids),
            (QuestionVisitEvent, "session_id", session_ids),
            (AssessmentEvent, "session_id", session_ids),
            (Attempt, "pk", attempt_ids),
            (StudentQuestionAssignment, "session_id", session_ids),
            (ClientTimeSync, "session_id", session_ids),
            (ExperimentSession, "pk", session_ids),
        ):
            if ids:
                counts[model.__name__.lower()] = model.objects.filter(**{f"{field}__in": ids}).count() if field != "pk" else len(ids)
                model.objects.filter(**{f"{field}__in": ids})._raw_delete(using="default")
        affected_jobs = list(ExportJob.objects.filter(course_id=course_id))
        for job in affected_jobs:
            if job.artifact_path and os.path.exists(job.artifact_path):
                os.remove(job.artifact_path)
            job.status = ExportJob.Status.INVALIDATED
            job.artifact_path = ""
            job.artifact_sha256 = ""
            job.save(update_fields=["status", "artifact_path", "artifact_sha256"])
        purge_request.status = DataPurgeRequest.Status.EXECUTED
        purge_request.executed_by = executed_by
        purge_request.executed_at = timezone.now()
        purge_request.result_counts = counts
        purge_request.save(update_fields=["status", "executed_by", "executed_at", "result_counts"])
    return counts


def cleanup_expired_exports(*, now=None):
    now = now or timezone.now()
    removed = 0
    for job in ExportJob.objects.filter(expires_at__lte=now).exclude(status=ExportJob.Status.EXPIRED):
        if job.artifact_path and os.path.exists(job.artifact_path):
            os.remove(job.artifact_path)
            removed += 1
        job.status = ExportJob.Status.EXPIRED
        job.artifact_path = ""
        job.artifact_sha256 = ""
        job.save(update_fields=["status", "artifact_path", "artifact_sha256"])
    return removed
