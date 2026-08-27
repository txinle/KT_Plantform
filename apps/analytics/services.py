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
    GradeRecord,
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


class ExportContractError(ValueError):
    pass


UNRECORDED_CLASS_FILTER = "__unrecorded__"


def _class_snapshot_filter_value(class_name):
    return "" if class_name == UNRECORDED_CLASS_FILTER else class_name


def create_export_snapshot(*, course=None, experiment=None, research_study=None,
                           created_by, filter_definition=None):
    versions = []
    if experiment is not None:
        versions = list(experiment.versions.order_by("version_number").values_list("pk", "version_number"))
    cutoff = timezone.now()
    scope = {"session__version__experiment_id": experiment.pk} if experiment is not None else {"session__version__experiment__course_id": course.pk}
    if research_study is not None:
        course_ids = list(research_study.course_links.values_list("course_id", flat=True))
        scope = {"session__version__experiment__course_id__in": course_ids}
    watermark = AssessmentEvent.objects.filter(**scope, server_received_at__lte=cutoff).aggregate(max_seq=models.Max("server_ingest_sequence_no"))["max_seq"] or 0
    schema_hashes = {
        f"{row.event_type}:v{row.event_version}": row.schema_sha256
        for row in EventSchemaArtifact.objects.all().order_by("event_type", "event_version")
    }
    schema_manifest_hash = hashlib.sha256(json.dumps(schema_hashes, sort_keys=True).encode()).hexdigest()
    return ExportSnapshot.objects.create(
        course=course,
        experiment=experiment,
        research_study=research_study,
        created_by=created_by,
        cutoff_at=cutoff,
        source_cutoff_at=cutoff,
        event_high_watermark=watermark,
        schema_manifest_hash=schema_manifest_hash,
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
    filter_display = dict(snapshot.filter_definition)
    if filter_display.get("class_name") == UNRECORDED_CLASS_FILTER:
        filter_display["class_name"] = "未记录班级"
    provenance = {
        "export_snapshot": {"id": str(snapshot.snapshot_id), "source_cutoff_at": snapshot.source_cutoff_at.isoformat(), "event_high_watermark": snapshot.event_high_watermark},
        "schema": {"manifest_hash": snapshot.schema_manifest_hash, "event_schema_hashes": schemas},
        "derivation": {"version": "v1", "deterministic": True, "code_sha": _git_commit()},
        "source_records": {"sessions": "assessment.ExperimentSession", "attempts": "assessment.Attempt", "events": "assessment.AssessmentEvent", "grades": "assessment.GradeRecord", "confidence": "assessment.ConfidenceAssignment"},
    }
    return {
        "manifest_version": 2,
        "contains_direct_identifiers": True,
        "identity_columns": ["student_number", "student_name"],
        "primary_question_identifier": "question_code",
        "export_snapshot_id": str(snapshot.snapshot_id),
        "course_id": snapshot.course_id,
        "experiment_id": snapshot.experiment_id,
        "experiment_name": experiment_name,
        "version_number": version_number,
        "research_study_id": snapshot.research_study_id,
        "cutoff_at": snapshot.cutoff_at.isoformat(),
        "source_cutoff_at": snapshot.source_cutoff_at.isoformat(),
        "event_high_watermark": snapshot.event_high_watermark,
        "schema_manifest_hash": snapshot.schema_manifest_hash,
        "database_snapshot_strategy": snapshot.database_snapshot_strategy,
        "filter_definition": snapshot.filter_definition,
        "filter_display": filter_display,
        "selected_tables": list(selected_tables),
        "event_schema_hashes": schemas,
        "application_git_commit_sha": _git_commit(),
        "export_config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "row_counts": row_counts or {},
        "data_status": data_status or {},
        "provenance": provenance,
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
    """Build a deterministic, identified ZIP from the job's frozen cutoff."""
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
    if filter_definition.get("class_name"):
        session_filter["class_snapshot_name"] = _class_snapshot_filter_value(filter_definition["class_name"])
    if filter_definition.get("student_number"):
        session_filter["student__student_number"] = filter_definition["student_number"]
    if filter_definition.get("session_id"):
        session_filter["pk"] = int(filter_definition["session_id"])
    if filter_definition.get("status") in {choice for choice, _label in ExperimentSession.Status.choices}:
        session_filter["status"] = filter_definition["status"]
    with transaction.atomic():
        if connection_vendor := connection.vendor:
            if connection_vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        sessions = list(ExperimentSession.objects.filter(**session_filter, started_at__lte=snapshot.source_cutoff_at).select_related("student", "version"))
        session_ids = [s.pk for s in sessions]
        attempt_queryset = Attempt.objects.filter(session_id__in=session_ids, session__started_at__lte=snapshot.source_cutoff_at).filter(models.Q(submitted_at__isnull=True) | models.Q(submitted_at__lte=snapshot.source_cutoff_at))
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            attempt_queryset = attempt_queryset.filter(status=filter_definition["status"])
        attempts = list(attempt_queryset.select_related("session", "assignment__snapshot", "assignment__item_instance"))
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            selected_session_ids = {attempt.session_id for attempt in attempts}
            sessions = [session for session in sessions if session.pk in selected_session_ids]
            session_ids = [session.pk for session in sessions]
        events = list(AssessmentEvent.objects.filter(**({"session__in": session_ids}), server_received_at__lte=snapshot.source_cutoff_at, server_ingest_sequence_no__lte=snapshot.event_high_watermark).order_by("session_id", "server_ingest_sequence_no"))
        assignments_queryset = StudentQuestionAssignment.objects.filter(session_id__in=session_ids).select_related("snapshot", "item_instance")
        if filter_definition.get("status") in {choice for choice, _label in Attempt.Status.choices}:
            assignments_queryset = assignments_queryset.filter(attempt__in=attempts)
        assignments = list(assignments_queryset)
        _validate_question_codes(assignments)
        confidence_assignments = list(
            ConfidenceAssignment.objects.filter(
                attempt_id__in=[a.pk for a in attempts],
                assigned_at__lte=snapshot.source_cutoff_at,
            )
        )
        simulations = list(
            SimulationSnapshot.objects.filter(
                attempt_id__in=[a.pk for a in attempts],
                created_at__lte=snapshot.source_cutoff_at,
            )
        )
    roster_rows = []
    if job.kind == ExportJob.Kind.IDENTITY:
        selected_tables = ["student_roster"]
        students = list(job.experiment.students.select_related("class_group")) if job.experiment_id else []
        roster_rows = [[student.student_number, student.shown_name, str(student.class_group or "")] for student in students]
    elif job.selected_tables:
        selected_tables = list(job.selected_tables)
    else:
        selected_tables = list(PACKAGE_TABLES.get(job.package, PACKAGE_TABLES["complete"]))
    rows = _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations, snapshot=snapshot)
    row_counts = {
        table: len(roster_rows) if table == "student_roster" else len(rows[table][1])
        for table in selected_tables
        if table == "student_roster" or table in rows
    }
    data_status = {
        table: ("no_observation" if row_counts.get(table, 0) == 0 else "observed")
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
        archive.writestr("00_manifest/derivation_runs.json", manifest_bytes({
            "derivation_version": "v1",
            "deterministic": True,
            "source_snapshot_id": str(snapshot.snapshot_id),
            "source_cutoff_at": snapshot.source_cutoff_at.isoformat(),
            "event_high_watermark": snapshot.event_high_watermark,
            "schema_manifest_hash": snapshot.schema_manifest_hash,
            "application_git_commit_sha": _git_commit(),
        }))
        archive.writestr("01_design_metadata/README.txt", "This package contains direct student identifiers and must be handled as identified research data.\n")
        if job.kind == ExportJob.Kind.IDENTITY:
            archive.writestr(
                "01_identity/student_roster.csv",
                _csv_bytes(
                    ["student_number", "student_name", "class_name"],
                    roster_rows,
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


def _validate_question_codes(assignments):
    seen = {}
    for assignment in assignments:
        snapshot = assignment.snapshot
        code = snapshot.code.strip()
        if not code:
            raise ExportContractError(f"题目快照 {snapshot.pk} 缺少 question_code")
        key = (snapshot.version_id, code)
        previous_snapshot_id = seen.setdefault(key, snapshot.pk)
        if previous_snapshot_id != snapshot.pk:
            raise ExportContractError(f"同一实验版本题目编码重复：{code}")


def preview_export_table(*, course, experiment, table, version_id=None,
                         class_name=None, student_number=None, session_id=None, status=None,
                         page=1, page_size=20):
    """Return a paginated, experiment-scoped preview using export row logic."""
    filter_definition = {
        key: value
        for key, value in {
            "version_id": version_id,
            "class_name": class_name,
            "student_number": student_number,
            "session_id": session_id,
            "status": status,
        }.items()
        if value not in (None, "")
    }
    session_filter = {"version__experiment_id": experiment.pk}
    if version_id:
        session_filter["version_id"] = int(version_id)
    if class_name:
        session_filter["class_snapshot_name"] = _class_snapshot_filter_value(class_name)
    if student_number:
        session_filter["student__student_number"] = student_number
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


def _table_rows(job, sessions, assignments, attempts, events, confidence_assignments, simulations, snapshot=None):
    identity_headers = ("student_number", "student_name")
    session_by_id = {session.pk: session for session in sessions}
    attempt_by_assignment = {attempt.assignment_id: attempt for attempt in attempts}
    attempt_by_id = {attempt.pk: attempt for attempt in attempts}
    snapshot_code_by_id = {assignment.snapshot_id: assignment.snapshot.code for assignment in assignments}

    def identity_for_session(session_id):
        session = session_by_id[session_id]
        return [session.student.student_number or "", session.student.shown_name]

    def question_code_for_snapshot(snapshot_id):
        return snapshot_code_by_id.get(snapshot_id, "")

    subject_values = []
    seen_student_numbers = set()
    for session in sessions:
        student_number = session.student.student_number or ""
        if student_number not in seen_student_numbers:
            subject_values.append([student_number, session.student.shown_name])
            seen_student_numbers.add(student_number)

    selected_attempt_ids = set(attempt_by_id)
    selected_events = [event for event in events if event.attempt_id is None or event.attempt_id in selected_attempt_ids]
    selected_confidence = [assignment for assignment in confidence_assignments if assignment.attempt_id in selected_attempt_ids]
    selected_simulations = [simulation for simulation in simulations if simulation.attempt_id in selected_attempt_ids]

    event_headers = (
        *identity_headers, "question_code", "event_id", "event_type", "event_version", "session_id", "attempt_id",
        "client_instance_id", "client_sequence_no", "server_ingest_sequence_no", "correlation_id",
        "server_received_at", "capture_mode", "quality_flags", "payload", "event_domain",
        "actor_source", "envelope_version", "assignment_id", "question_snapshot_id", "item_instance_id",
        "client_wall_time_utc", "client_monotonic_ms", "client_time_origin", "time_sync_id",
        "estimated_clock_offset_ms", "estimated_rtt_ms", "estimated_occurred_at_utc", "server_committed_at",
        "app_version",
    )

    def event_row(event):
        return [
            *identity_for_session(event.session_id), question_code_for_snapshot(event.question_snapshot_id),
            event.event_id, event.event_type, event.event_version, event.session_id, event.attempt_id,
            event.client_instance_id, event.client_sequence_no, event.server_ingest_sequence_no,
            event.correlation_id, event.server_received_at.isoformat(), event.capture_mode,
            json.dumps(event.quality_flags, ensure_ascii=False), json.dumps(event.payload, ensure_ascii=False),
            event.event_domain, event.actor_source, event.envelope_version, event.assignment_id,
            event.question_snapshot_id, event.item_instance_id,
            event.client_wall_time_utc.isoformat() if event.client_wall_time_utc else "",
            event.client_monotonic_ms, event.client_time_origin.isoformat() if event.client_time_origin else "",
            event.time_sync_id, event.estimated_clock_offset_ms, event.estimated_rtt_ms,
            event.estimated_occurred_at_utc.isoformat() if event.estimated_occurred_at_utc else "",
            event.server_committed_at.isoformat() if event.server_committed_at else "", event.app_version,
        ]

    event_rows = [event_row(event) for event in selected_events]
    compact_event_headers = (*identity_headers, "question_code", "event_id", "event_type", "session_id", "attempt_id", "server_received_at", "payload")

    def compact_event_row(event):
        return [
            *identity_for_session(event.session_id), question_code_for_snapshot(event.question_snapshot_id),
            event.event_id, event.event_type, event.session_id, event.attempt_id,
            event.server_received_at.isoformat(), json.dumps(event.payload, ensure_ascii=False),
        ]

    response_rows = [compact_event_row(event) for event in selected_events if event.event_type in RESPONSE_EVENT_TYPES]
    exposure_rows = [compact_event_row(event) for event in selected_events if event.event_type in EXPOSURE_EVENT_TYPES]
    navigation_rows = [compact_event_row(event) for event in selected_events if event.event_type in ("item_entered", "item_left")]
    telemetry_rows = [compact_event_row(event) for event in selected_events if event.event_domain == AssessmentEvent.EventDomain.TELEMETRY]
    simulation_event_rows = [compact_event_row(event) for event in selected_events if event.event_type in SIMULATION_EVENT_TYPES]

    def identity_and_question_for_attempt(attempt_id):
        attempt = attempt_by_id[attempt_id]
        return [*identity_for_session(attempt.session_id), attempt.assignment.snapshot.code]

    grade_by_attempt = {}
    if selected_attempt_ids:
        for grade in GradeRecord.objects.filter(
            submission__attempt_id__in=selected_attempt_ids,
            created_at__lte=(snapshot.source_cutoff_at if snapshot is not None else timezone.now()),
        ).select_related("submission").order_by("submission_id", "-version"):
            grade_by_attempt.setdefault(grade.submission.attempt_id, grade)
    rows = {
        "subjects": (identity_headers, subject_values),
        "sessions": ((*identity_headers, "session_id", "experiment_id", "version", "status", "started_at", "completed_at"), [[*identity_for_session(session.pk), session.pk, session.version.experiment_id, session.version.version_number, session.status, session.started_at.isoformat(), session.completed_at.isoformat() if session.completed_at else ""] for session in sessions]),
        "assignments": ((*identity_headers, "question_code", "assignment_id", "session_id", "question_snapshot_id", "item_instance_id", "assigned_sequence_index"), [[*identity_for_session(assignment.session_id), assignment.snapshot.code, assignment.pk, assignment.session_id, assignment.snapshot_id, assignment.item_instance_id, assignment.assigned_sequence_index] for assignment in assignments]),
        "item_instances": (("question_code", "item_instance_id", "question_snapshot_id", "presentation_variant", "option_order", "parameter_values", "random_seed", "presentation_hash"), [[assignment.snapshot.code, assignment.item_instance_id, assignment.snapshot_id, assignment.item_instance.presentation_variant, json.dumps(assignment.item_instance.option_order), json.dumps(assignment.item_instance.parameter_values), assignment.item_instance.random_seed, assignment.item_instance.presentation_hash] for assignment in _unique_assignments(assignments, "item_instance_id")]),
        "attempts": ((*identity_headers, "question_code", "attempt_id", "session_id", "question_snapshot_id", "item_instance_id", "status", "first_opened_at", "last_opened_at", "visit_count", "draft_answer", "draft_saved_at", "final_answer", "submitted_at", "first_to_submit_duration_ms", "last_visit_to_submission_ms", "is_correct", "score", "first_open_sequence_index", "submission_sequence_index", "current_response_snapshot_id", "final_response_snapshot_id", "submission_event_id", "submission_grade_status", "grade_version", "grade_is_correct", "grade_score", "grade_max_score", "normalized_response", "grading_method", "grading_version", "grader_version", "graded_at"), [_attempt_export_row(assignment, attempt_by_assignment.get(assignment.pk), session_by_id, grade_by_attempt) for assignment in assignments if assignment.session_id in session_by_id]),
        "assessment_events": (event_headers, event_rows),
        "response_events": (compact_event_headers, response_rows),
        "exposure_events": (compact_event_headers, exposure_rows),
        "navigation_events": (compact_event_headers, navigation_rows),
        "telemetry_events": (compact_event_headers, telemetry_rows),
        "simulation_events": (compact_event_headers, simulation_event_rows),
        "simulation_snapshots": ((*identity_headers, "question_code", "snapshot_id", "attempt_id", "simulation_version", "state_metadata", "content_hash", "content_size", "created_at"), [[*identity_and_question_for_attempt(simulation.attempt_id), simulation.snapshot_id, simulation.attempt_id, simulation.simulation_version, json.dumps(simulation.state_metadata), simulation.content_hash, simulation.content_size, simulation.created_at.isoformat()] for simulation in selected_simulations]),
        "confidence_assignments": ((*identity_headers, "question_code", "confidence_assignment_id", "attempt_id", "condition", "assignment_unit", "assignment_version", "response_status", "probability", "assigned_at", "committed_at"), [[*identity_and_question_for_attempt(confidence.attempt_id), confidence.confidence_assignment_id, confidence.attempt_id, confidence.condition, confidence.assignment_unit, confidence.assignment_version, confidence.response_status, confidence.probability, confidence.assigned_at.isoformat(), confidence.committed_at.isoformat() if confidence.committed_at else ""] for confidence in selected_confidence]),
        "item_snapshots": (("question_code", "question_snapshot_id", "version_id", "stable_code", "question_type", "primary_kc", "all_kcs"), [[assignment.snapshot.code, assignment.snapshot_id, assignment.snapshot.version_id, assignment.snapshot.stable_code, assignment.snapshot.question_type, assignment.snapshot.primary_kc, json.dumps(assignment.snapshot.all_kcs, ensure_ascii=False)] for assignment in _unique_assignments(assignments, "snapshot_id")]),
        "item_kc_mapping": (("question_code", "question_snapshot_id", "primary_kc", "all_kcs"), [[assignment.snapshot.code, assignment.snapshot_id, assignment.snapshot.primary_kc, json.dumps(assignment.snapshot.all_kcs, ensure_ascii=False)] for assignment in _unique_assignments(assignments, "snapshot_id")]),
        "q_matrix": (("question_code", "question_snapshot_id", "primary_kc", "all_kcs"), [[assignment.snapshot.code, assignment.snapshot_id, assignment.snapshot.primary_kc, json.dumps(assignment.snapshot.all_kcs, ensure_ascii=False)] for assignment in _unique_assignments(assignments, "snapshot_id")]),
        "kc_dictionary": (("kc_id", "kc_name"), [[assignment.snapshot.primary_kc, assignment.snapshot.primary_kc] for assignment in _unique_assignments(assignments, "snapshot_id") if assignment.snapshot.primary_kc]),
    }
    derivation_id = str(getattr(snapshot, "snapshot_id", "preview"))
    base = (*identity_headers, "question_code", "attempt_id", "derivation_run_id", "derivation_version")
    feature_rows, kt_rows, cd_rows, cal_rows = [], [], [], []
    for attempt in attempts:
        ident = [*identity_and_question_for_attempt(attempt.pk), attempt.pk, derivation_id, "v1"]
        feature_rows.append([*ident, attempt.is_correct, attempt.score, attempt.visit_count, attempt.first_opened_at.isoformat() if attempt.first_opened_at else "", attempt.submitted_at.isoformat() if attempt.submitted_at else ""])
        outcome = "" if attempt.is_correct is None else int(bool(attempt.is_correct))
        kt_rows.append([*ident, attempt.assignment.assigned_sequence_index, outcome, attempt.assignment.snapshot.primary_kc or ""])
        response_value = attempt.final_answer if attempt.final_answer is not None else attempt.draft_answer
        cd_rows.append([*ident, json.dumps(response_value, ensure_ascii=False) if response_value is not None else "", json.dumps(attempt.assignment.snapshot.all_kcs, ensure_ascii=False)])
        confidence = next((c for c in selected_confidence if c.attempt_id == attempt.pk), None)
        probability = confidence.probability if confidence and confidence.probability is not None else ""
        error = (float(probability) / 100.0 - float(bool(attempt.is_correct))) if probability != "" and attempt.is_correct is not None else ""
        cal_rows.append([*ident, probability, attempt.is_correct, error, confidence.condition if confidence else ""])
    rows.update({
        "attempt_features": (base + ("is_correct", "score", "visit_count", "first_opened_at", "submitted_at"), feature_rows),
        "kt_sequences": (base + ("sequence_index", "outcome", "primary_kc"), kt_rows),
        "cd_response_matrix": (base + ("response", "all_kcs"), cd_rows),
        "calibration_features": (base + ("confidence_probability", "is_correct", "calibration_error", "condition"), cal_rows),
    })
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


def _attempt_export_row(assignment, attempt, session_by_id, grade_by_attempt=None):
    session = session_by_id[assignment.session_id]
    identity = [session.student.student_number or "", session.student.shown_name, assignment.snapshot.code]
    linkage = [assignment.session_id, assignment.snapshot.pk, assignment.item_instance_id]
    if attempt is None:
        return [*identity, "", *linkage] + [""] * 28
    grade = (grade_by_attempt or {}).get(attempt.pk)
    submission = getattr(attempt, "submission_event", None)
    if attempt is None:
        return [*identity, "", *linkage, *([""] * 14)]
    return [
        *identity,
        attempt.pk,
        *linkage,
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
        attempt.current_response_snapshot_id,
        attempt.final_response_snapshot_id,
        submission.pk if submission else "",
        submission.grading_status if submission else "",
        grade.version if grade else "",
        grade.is_correct if grade else "",
        grade.score if grade else "",
        grade.max_score if grade else "",
        json.dumps(grade.normalized_response, ensure_ascii=False) if grade and grade.normalized_response is not None else "",
        grade.grading_method if grade else "",
        grade.grading_version if grade else "",
        grade.grader_version if grade else "",
        grade.graded_at.isoformat() if grade and grade.graded_at else "",
    ]


def _finalize_export_job(job, zip_path, manifest):
    digest = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    job.status = ExportJob.Status.SUCCEEDED
    job.finished_at = timezone.now()
    job.progress_stage = "complete"
    job.artifact_path = zip_path
    job.artifact_sha256 = digest
    job.save(update_fields=["status", "finished_at", "progress_stage", "artifact_path", "artifact_sha256"])
    return job


def execute_purge_request(purge_request, *, executed_by):
    from apps.accounts.models import DataPurgeRequest
    from apps.assessment.models import (
        AssessmentEvent, Attempt, ClientTimeSync, ConfidenceAssignment,
        DraftSaveEvent, ExperimentSession, QuestionVisitEvent, ResponseSnapshot,
        SimulationSnapshot, StudentQuestionAssignment, SubmissionEvent, GradeRecord,
    )
    course_id = purge_request.scope_definition.get("course_id")
    if not course_id:
        raise ValueError("purge scope missing course_id")
    with transaction.atomic():
        affected_snapshots = list(
            ExportSnapshot.objects.filter(course_id=course_id)
        )
        session_ids = list(ExperimentSession.objects.filter(version__experiment__course_id=course_id).values_list("pk", flat=True))
        attempt_ids = list(Attempt.objects.filter(session_id__in=session_ids).values_list("pk", flat=True))
        counts = {"sessions": len(session_ids), "attempts": len(attempt_ids), "events": AssessmentEvent.objects.filter(session_id__in=session_ids).count()}
        for model, field, ids in (
            (AssessmentEvent, "session_id", session_ids),
            (ConfidenceAssignment, "attempt_id", attempt_ids),
            (SimulationSnapshot, "attempt_id", attempt_ids),
            (DraftSaveEvent, "attempt_id", attempt_ids),
            (QuestionVisitEvent, "session_id", session_ids),
            (GradeRecord, "submission__attempt_id", attempt_ids),
            (SubmissionEvent, "attempt_id", attempt_ids),
            (ResponseSnapshot, "attempt_id", attempt_ids),
            (Attempt, "pk", attempt_ids),
            (StudentQuestionAssignment, "session_id", session_ids),
            (ClientTimeSync, "session_id", session_ids),
            (ExperimentSession, "pk", session_ids),
        ):
            if ids:
                counts[model.__name__.lower()] = model.objects.filter(**{f"{field}__in": ids}).count() if field != "pk" else len(ids)
                model.objects.filter(**{f"{field}__in": ids})._raw_delete(using="default")
        affected_jobs = list(ExportJob.objects.filter(course_id=course_id))
        invalidated_at = timezone.now()
        for snapshot in affected_snapshots:
            ExportSnapshot.objects.filter(pk=snapshot.pk).update(
                invalidated_at=invalidated_at,
                invalidated_reason=f"purge:{purge_request.reason_code}",
            )
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
