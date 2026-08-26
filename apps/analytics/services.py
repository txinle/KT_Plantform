import hashlib
import json
import csv
import io
import os
import subprocess
import zipfile
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from apps.assessment.models import (
    AssessmentEvent, Attempt, ExperimentSession, EventSchemaArtifact,
    StudentQuestionAssignment, ConfidenceAssignment, SimulationSnapshot,
)
from apps.experiments.models import ItemInstance, QuestionSnapshot
from .export_catalog import (
    EXPOSURE_EVENT_TYPES,
    PACKAGE_TABLES,
    PLACEHOLDER_TABLES,
    RESPONSE_EVENT_TYPES,
    SIMULATION_EVENT_TYPES,
    TABLE_PATHS,
)

from .models import ExportJob, ExportSnapshot


def create_export_snapshot(*, course=None, experiment=None, research_study=None,
                           created_by, filter_definition=None):
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
        filter_definition=filter_definition or {},
    )


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_export_manifest(snapshot, *, selected_tables, row_counts=None, data_status=None,
                          experiment_name=None, version_number=None):
    schemas = {
        f"{row.event_type}:v{row.event_version}": row.schema_sha256
        for row in EventSchemaArtifact.objects.all()
    }
    config = {
        "selected_tables": list(selected_tables),
        "selected_versions": snapshot.selected_versions,
        "filter_definition": snapshot.filter_definition,
    }
    return {
        "manifest_version": 1,
        "export_snapshot_id": str(snapshot.snapshot_id),
        "course_id": snapshot.course_id,
        "experiment_id": snapshot.experiment_id,
        "experiment_name": experiment_name,
        "version_number": version_number,
        "research_study_id": snapshot.research_study_id,
        "cutoff_at": snapshot.cutoff_at.isoformat(),
        "source_cutoff_at": snapshot.cutoff_at.isoformat(),
        "database_snapshot_strategy": snapshot.database_snapshot_strategy,
        "filter_definition": snapshot.filter_definition,
        "selected_tables": list(selected_tables),
        "event_schema_hashes": schemas,
        "application_git_commit_sha": _git_commit(),
        "export_config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "row_counts": row_counts or {},
        "data_status": data_status or {},
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
    study_student_ids = list(job.research_study.participants.values_list("student_id", flat=True)) if job.research_study_id else []
    filter_definition = snapshot.filter_definition or {}
    session_filter = {"version__experiment_id": job.experiment_id} if job.experiment_id else ({"version__experiment__course_id__in": study_course_ids, "student_id__in": study_student_ids} if study_course_ids else {"version__experiment__course_id": job.course_id})
    if filter_definition.get("version_id"):
        session_filter["version_id"] = int(filter_definition["version_id"])
    if filter_definition.get("student_id"):
        session_filter["student_id"] = int(filter_definition["student_id"])
    if filter_definition.get("session_id"):
        session_filter["pk"] = int(filter_definition["session_id"])
    if filter_definition.get("status") in {choice for choice, _label in ExperimentSession.Status.choices}:
        session_filter["status"] = filter_definition["status"]
    with transaction.atomic():
        if connection_vendor := connection.vendor:
            if connection_vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        sessions = list(ExperimentSession.objects.filter(**session_filter, started_at__lte=snapshot.cutoff_at).select_related("student", "version"))
        session_ids = [s.pk for s in sessions]
        attempt_queryset = Attempt.objects.filter(session_id__in=session_ids, session__started_at__lte=snapshot.cutoff_at).filter(models.Q(submitted_at__isnull=True) | models.Q(submitted_at__lte=snapshot.cutoff_at))
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            attempt_queryset = attempt_queryset.filter(status=filter_definition["status"])
        attempts = list(attempt_queryset.select_related("session", "assignment__snapshot", "assignment__item_instance"))
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            selected_session_ids = {attempt.session_id for attempt in attempts}
            sessions = [session for session in sessions if session.pk in selected_session_ids]
            session_ids = [session.pk for session in sessions]
        events = list(AssessmentEvent.objects.filter(**({"session__in": session_ids}), server_received_at__lte=snapshot.cutoff_at).order_by("session_id", "server_ingest_sequence_no"))
        assignments_queryset = StudentQuestionAssignment.objects.filter(session_id__in=session_ids).select_related("snapshot", "item_instance")
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            assignments_queryset = assignments_queryset.filter(attempt__in=attempts)
        assignments = list(assignments_queryset)
        confidence_assignments = list(
            ConfidenceAssignment.objects.filter(
                attempt_id__in=[a.pk for a in attempts],
                assigned_at__lte=snapshot.cutoff_at,
            )
        )
        simulations = list(
            SimulationSnapshot.objects.filter(
                attempt_id__in=[a.pk for a in attempts],
                created_at__lte=snapshot.cutoff_at,
            )
        )
    if job.kind == ExportJob.Kind.IDENTITY:
        selected_tables = ["identity_mapping"]
    elif job.selected_tables:
        selected_tables = list(job.selected_tables)
    else:
        selected_tables = list(PACKAGE_TABLES.get(job.package, PACKAGE_TABLES["complete"]))
    rows = _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations)
    row_counts = {table: len(rows[table][1]) for table in selected_tables if table in rows}
    data_status = {
        table: "placeholder" if table in PLACEHOLDER_TABLES else ("no_observation" if row_counts.get(table, 0) == 0 else "observed")
        for table in selected_tables
    }
    selected_version_number = None
    if filter_definition.get("version_id"):
        selected_version_number = next(
            (item["version"] for item in snapshot.selected_versions if str(item["id"]) == str(filter_definition["version_id"])),
            None,
        )
    elif len(snapshot.selected_versions) == 1:
        selected_version_number = snapshot.selected_versions[0]["version"]
    manifest = build_export_manifest(
        snapshot,
        selected_tables=selected_tables,
        row_counts=row_counts,
        data_status=data_status,
        experiment_name=job.experiment.name if job.experiment_id else None,
        version_number=selected_version_number,
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("00_manifest/manifest.json", manifest_bytes(manifest))
        archive.writestr("01_design_metadata/README.txt", "All identifiers in this package are pseudonymized. Identity linkage is managed separately.\n")
        if job.kind == ExportJob.Kind.IDENTITY:
            students = list(job.experiment.students.select_related("class_group")) if job.experiment_id else []
            from apps.accounts.models import CourseSubject
            subject_ids = {
                student.pk: str(CourseSubject.objects.get_or_create(course_id=job.course_id, student_id=student.pk)[0].public_uuid)
                for student in students
            }
            archive.writestr(
                "01_identity/identity_mapping.csv",
                _csv_bytes(
                    ["pseudonymized_student_id", "student_number", "student_name", "class_name"],
                    [[subject_ids[s.id], s.student_number, s.shown_name, str(s.class_group or "")] for s in students],
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


def preview_export_table(*, course, experiment, table, version_id=None,
                         student_id=None, session_id=None, status=None,
                         page=1, page_size=20):
    """Return a paginated, experiment-scoped preview using export row logic."""
    filter_definition = {
        key: value
        for key, value in {
            "version_id": version_id,
            "student_id": student_id,
            "session_id": session_id,
            "status": status,
        }.items()
        if value not in (None, "")
    }
    session_filter = {"version__experiment_id": experiment.pk}
    if version_id:
        session_filter["version_id"] = int(version_id)
    if student_id:
        session_filter["student_id"] = int(student_id)
    if session_id:
        session_filter["pk"] = int(session_id)
    if status in {choice for choice, _label in ExperimentSession.Status.choices}:
        session_filter["status"] = status
    sessions = list(
        ExperimentSession.objects.filter(**session_filter)
        .select_related("student", "version")
        .order_by("pk")
    )
    session_ids = [session.pk for session in sessions]
    attempt_queryset = Attempt.objects.filter(session_id__in=session_ids).select_related(
        "session", "assignment__snapshot", "assignment__item_instance"
    )
    if status in {choice for choice, _label in Attempt.Status.choices}:
        attempt_queryset = attempt_queryset.filter(status=status)
    attempts = list(attempt_queryset.order_by("pk"))
    if status in {choice for choice, _label in Attempt.Status.choices}:
        selected_session_ids = {attempt.session_id for attempt in attempts}
        sessions = [session for session in sessions if session.pk in selected_session_ids]
        session_ids = [session.pk for session in sessions]
    attempt_ids = {attempt.pk for attempt in attempts}
    assignments = list(
        StudentQuestionAssignment.objects.filter(session_id__in=session_ids)
        .select_related("snapshot", "item_instance")
        .order_by("session_id", "assigned_sequence_index")
    )
    if status in {choice for choice, _label in Attempt.Status.choices}:
        assignment_ids = {attempt.assignment_id for attempt in attempts}
        assignments = [assignment for assignment in assignments if assignment.pk in assignment_ids]
    events = list(
        AssessmentEvent.objects.filter(
            session_id__in=session_ids,
            server_received_at__lte=timezone.now(),
        ).order_by("session_id", "server_ingest_sequence_no")
    )
    confidence_assignments = list(ConfidenceAssignment.objects.filter(attempt_id__in=attempt_ids))
    simulations = list(SimulationSnapshot.objects.filter(attempt_id__in=attempt_ids))
    fake_job = SimpleNamespace(course_id=course.pk, research_study_id=None)
    rows = _table_rows(fake_job, sessions, assignments, attempts, events, confidence_assignments, simulations)
    headers, values = rows.get(table, ((), []))
    total = len(values)
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    start = (page - 1) * page_size
    return {
        "table": table,
        "headers": headers,
        "rows": values[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_previous": page > 1,
        "has_next": start + page_size < total,
        "filter_definition": filter_definition,
        "data_status": "placeholder" if table in PLACEHOLDER_TABLES else ("no_observation" if total == 0 else "observed"),
    }


def _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations):
    from apps.accounts.models import CourseSubject, ResearchStudyParticipant
    student_ids = {s.student_id for s in sessions}
    if job.research_study_id:
        subject_map = {
            row.student_id: str(row.public_uuid)
            for row in ResearchStudyParticipant.objects.filter(study_id=job.research_study_id, student_id__in=student_ids)
        }
    else:
        subject_map = {
            row.student_id: str(row.public_uuid)
            for row in CourseSubject.objects.filter(course_id=job.course_id, student_id__in=student_ids)
        }
        missing = student_ids - subject_map.keys()
        for student_id in missing:
            subject_map[student_id] = str(CourseSubject.objects.get_or_create(course_id=job.course_id, student_id=student_id)[0].public_uuid)
    subject_for = lambda student_id: subject_map.get(student_id, "")
    subject_values = []
    seen_subjects = set()
    for session in sessions:
        subject_id = subject_for(session.student_id)
        if subject_id and subject_id not in seen_subjects:
            subject_values.append([subject_id])
            seen_subjects.add(subject_id)
    session_by_id = {s.pk: s for s in sessions}
    attempt_by_assignment = {a.assignment_id: a for a in attempts}
    selected_attempt_ids = {a.pk for a in attempts}
    selected_events = [e for e in events if e.attempt_id is None or e.attempt_id in selected_attempt_ids]
    selected_confidence = [c for c in confidence_assignments if c.attempt_id in selected_attempt_ids]
    selected_simulations = [s for s in simulations if s.attempt_id in selected_attempt_ids]
    event_rows = [[
        e.event_id, e.event_type, e.event_version, e.session_id, e.attempt_id,
        e.client_instance_id, e.client_sequence_no, e.server_ingest_sequence_no,
        e.correlation_id, e.server_received_at.isoformat(), e.capture_mode,
        json.dumps(e.quality_flags, ensure_ascii=False), json.dumps(e.payload, ensure_ascii=False),
        e.event_domain, e.actor_source, e.envelope_version, e.assignment_id,
        e.question_snapshot_id, e.item_instance_id,
        e.client_wall_time_utc.isoformat() if e.client_wall_time_utc else "",
        e.client_monotonic_ms, e.client_time_origin.isoformat() if e.client_time_origin else "",
        e.time_sync_id, e.estimated_clock_offset_ms, e.estimated_rtt_ms,
        e.estimated_occurred_at_utc.isoformat() if e.estimated_occurred_at_utc else "",
        e.server_committed_at.isoformat() if e.server_committed_at else "", e.app_version,
    ] for e in selected_events]
    response_rows = [row for row, event in zip(event_rows, selected_events) if event.event_type in RESPONSE_EVENT_TYPES]
    exposure_rows = [row for row, event in zip(event_rows, selected_events) if event.event_type in EXPOSURE_EVENT_TYPES]
    telemetry_rows = [row for row, event in zip(event_rows, selected_events) if event.event_domain == AssessmentEvent.EventDomain.TELEMETRY]
    simulation_event_rows = [row for row, event in zip(event_rows, selected_events) if event.event_type in SIMULATION_EVENT_TYPES]
    subject_header = "study_subject_id" if job.research_study_id else "course_subject_id"
    rows = {
        "subjects": ((subject_header,), subject_values),
        "sessions": (("session_id", "subject_id", "experiment_id", "version", "status", "started_at", "completed_at"), [[s.pk, subject_for(s.student_id), s.version.experiment_id, s.version.version_number, s.status, s.started_at.isoformat(), s.completed_at.isoformat() if s.completed_at else ""] for s in sessions]),
        "assignments": (("assignment_id", "session_id", "question_snapshot_id", "item_instance_id", "assigned_sequence_index"), [[a.pk, a.session_id, a.snapshot_id, a.item_instance_id, a.assigned_sequence_index] for a in assignments]),
        "item_instances": (("item_instance_id", "question_snapshot_id", "presentation_variant", "option_order", "parameter_values", "random_seed", "presentation_hash"), [[a.item_instance_id, a.snapshot_id, a.item_instance.presentation_variant, json.dumps(a.item_instance.option_order), json.dumps(a.item_instance.parameter_values), a.item_instance.random_seed, a.item_instance.presentation_hash] for a in _unique_assignments(assignments, "item_instance_id")]),
        "attempts": (("attempt_id", "session_id", "subject_id", "question_snapshot_id", "item_instance_id", "question_code", "status", "first_opened_at", "last_opened_at", "visit_count", "draft_answer", "draft_saved_at", "final_answer", "submitted_at", "first_to_submit_duration_ms", "last_visit_to_submission_ms", "is_correct", "score", "first_open_sequence_index", "submission_sequence_index"), [_attempt_export_row(a, attempt_by_assignment.get(a.pk), session_by_id, subject_for) for a in assignments if a.session_id in session_by_id]),
        "assessment_events": (("event_id", "event_type", "event_version", "session_id", "attempt_id", "client_instance_id", "client_sequence_no", "server_ingest_sequence_no", "correlation_id", "server_received_at", "capture_mode", "quality_flags", "payload", "event_domain", "actor_source", "envelope_version", "assignment_id", "question_snapshot_id", "item_instance_id", "client_wall_time_utc", "client_monotonic_ms", "client_time_origin", "time_sync_id", "estimated_clock_offset_ms", "estimated_rtt_ms", "estimated_occurred_at_utc", "server_committed_at", "app_version"), event_rows),
        "response_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in response_rows]),
        "exposure_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in exposure_rows]),
        "navigation_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in event_rows if r[1] in ("item_entered", "item_left")]),
        "telemetry_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in telemetry_rows]),
        "simulation_events": (("event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload"), [[r[0], r[1], r[3], r[4], r[9], r[12]] for r in simulation_event_rows]),
        "simulation_snapshots": (("snapshot_id", "attempt_id", "simulation_version", "state_metadata", "content_hash", "content_size", "created_at"), [[s.snapshot_id, s.attempt_id, s.simulation_version, json.dumps(s.state_metadata), s.content_hash, s.content_size, s.created_at.isoformat()] for s in selected_simulations]),
        "confidence_assignments": (("confidence_assignment_id", "attempt_id", "condition", "assignment_unit", "assignment_version", "response_status", "probability", "assigned_at", "committed_at"), [[c.confidence_assignment_id, c.attempt_id, c.condition, c.assignment_unit, c.assignment_version, c.response_status, c.probability, c.assigned_at.isoformat(), c.committed_at.isoformat() if c.committed_at else ""] for c in selected_confidence]),
        "item_snapshots": (("question_snapshot_id", "version_id", "code", "question_type", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.version_id, a.snapshot.code, a.snapshot.question_type, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in _unique_assignments(assignments, "snapshot_id")]),
        "item_kc_mapping": (("question_snapshot_id", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in _unique_assignments(assignments, "snapshot_id")]),
        "q_matrix": (("question_snapshot_id", "primary_kc", "all_kcs"), [[a.snapshot_id, a.snapshot.primary_kc, json.dumps(a.snapshot.all_kcs, ensure_ascii=False)] for a in _unique_assignments(assignments, "snapshot_id")]),
        "kc_dictionary": (("kc_id", "kc_name"), [[a.snapshot.primary_kc, a.snapshot.primary_kc] for a in _unique_assignments(assignments, "snapshot_id") if a.snapshot.primary_kc]),
    }
    rows.update({k: (("attempt_id",), [[a.pk] for a in attempts]) for k in ("attempt_features", "kt_sequences", "cd_response_matrix", "calibration_features")})
    return rows


def _unique_assignments(assignments, field):
    seen = set()
    result = []
    for assignment in assignments:
        value = getattr(assignment, field)
        if value not in seen:
            result.append(assignment)
            seen.add(value)
    return result


def _attempt_export_row(assignment, attempt, session_by_id, subject_for):
    session = session_by_id[assignment.session_id]
    base = [assignment.session_id, subject_for(session.student_id), assignment.snapshot.pk, assignment.item_instance_id, assignment.snapshot.code]
    if attempt is None:
        return [""] + base + [""] * 14
    return [
        attempt.pk,
        *base,
        attempt.status,
        attempt.first_opened_at.isoformat() if attempt.first_opened_at else "",
        attempt.last_opened_at.isoformat() if attempt.last_opened_at else "",
        attempt.visit_count,
        json.dumps(attempt.draft_answer, ensure_ascii=False) if attempt.draft_answer is not None else "",
        attempt.draft_saved_at.isoformat() if attempt.draft_saved_at else "",
        json.dumps(attempt.final_answer, ensure_ascii=False) if attempt.final_answer is not None else "",
        attempt.submitted_at.isoformat() if attempt.submitted_at else "",
        attempt.first_to_submit_duration_ms,
        attempt.last_visit_to_submission_ms,
        attempt.is_correct,
        attempt.score,
        attempt.first_open_sequence_index,
        attempt.submission_sequence_index,
    ]


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
