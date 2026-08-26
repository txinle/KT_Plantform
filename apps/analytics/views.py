import csv
import hashlib
import os
from io import BytesIO
from urllib.parse import quote

from django.http import FileResponse, HttpResponse
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from apps.accounts.permissions import role_required
from apps.accounts.course_access import accessible_courses
from apps.accounts.models import User
from apps.assessment.models import Attempt, QuestionVisitEvent
from apps.audit.services import log_action
from apps.experiments.models import Experiment
from .export_fields import FIELD_LABELS, field_values
from .models import ExportJob
from .services import create_export_snapshot, execute_purge_request, run_export_job
from apps.accounts.models import DataPurgeRequest, ResearchStudy
from apps.accounts.course_access import get_accessible_course, require_course_manager

def _experiment(request, pk):
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        qs = Experiment.objects.all()
    else:
        qs = Experiment.objects.filter(
            Q(course__in=accessible_courses(request.user))
            | Q(course__isnull=True, owner=request.user)
        )
    return get_object_or_404(qs, pk=pk)


def _identified_experiment(request, pk):
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        qs = Experiment.objects.all()
    else:
        qs = Experiment.objects.filter(owner=request.user)
    return get_object_or_404(qs, pk=pk)


@role_required("teacher", "admin")
@require_POST
def export_job_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    experiment = None
    experiment_id = request.POST.get("experiment_id")
    if experiment_id:
        experiment = get_object_or_404(course.experiments, pk=experiment_id)
    package = request.POST.get("package", "complete")
    if package == "identity":
        return HttpResponse("身份任务必须通过独立端点创建", status=400, content_type="text/plain; charset=utf-8")
    kind = ExportJob.Kind.IDENTITY if package == "identity" else ExportJob.Kind.RESEARCH
    if kind == ExportJob.Kind.IDENTITY and not (
        request.user.is_superuser
        or request.user.role == User.Role.ADMIN
        or course.owner_id == request.user.id
    ):
        return HttpResponse("身份任务仅限课程负责人或管理员", status=403, content_type="text/plain; charset=utf-8")
    snapshot = create_export_snapshot(course=course, experiment=experiment, created_by=request.user)
    job = _prepare_export_job(
        course=course,
        experiment=experiment,
        snapshot=snapshot,
        created_by=request.user,
        kind=kind,
        package=package,
        selected_tables=request.POST.getlist("tables"),
        selected_fields=request.POST.getlist("fields"),
    )
    run_export_job(job)
    if kind == ExportJob.Kind.IDENTITY:
        log_action(request, "identity.export_job_create", course, {"package": package, "experiment_id": experiment_id})
    return redirect("courses:data", course.pk)


@role_required("teacher", "admin")
@require_POST
def research_study_export_create(request, study_id):
    study = get_object_or_404(ResearchStudy, pk=study_id)
    if not (request.user.is_superuser or request.user.role == User.Role.ADMIN or study.owner_id == request.user.id):
        return HttpResponse("无权访问纵向研究项目", status=404)
    course = study.course_links.select_related("course").first().course if study.course_links.exists() else None
    if course is None:
        return HttpResponse("研究项目尚未关联课程", status=400)
    package = request.POST.get("package", "longitudinal")
    snapshot = create_export_snapshot(course=course, research_study=study, created_by=request.user)
    job = _prepare_export_job(
        course=course,
        research_study=study,
        snapshot=snapshot,
        created_by=request.user,
        package=package,
    )
    run_export_job(job)
    return redirect("courses:data", course.pk)


@role_required("teacher", "admin")
@require_POST
def identity_export_job_create(request, course_pk):
    experiment = get_object_or_404(Experiment.objects.select_related("course").filter(course_id=course_pk), pk=request.POST.get("experiment_id"))
    course = experiment.course
    if not (request.user.is_superuser or request.user.role == User.Role.ADMIN or course.owner_id == request.user.id):
        return HttpResponse("身份任务仅限课程负责人或管理员", status=403)
    snapshot = create_export_snapshot(course=course, experiment=experiment, created_by=request.user)
    job = _prepare_export_job(
        course=course,
        experiment=experiment,
        snapshot=snapshot,
        created_by=request.user,
        kind=ExportJob.Kind.IDENTITY,
        package="identity",
    )
    run_export_job(job)
    log_action(request, "identity.export_job_create", course, {"experiment_id": experiment.pk})
    return redirect("courses:data", course.pk)


@role_required("teacher", "admin")
@require_POST
def purge_request_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_course_manager(course, request.user)
    if request.POST.get("confirmation") != "PURGE":
        return HttpResponse("请输入确认短语 PURGE", status=400, content_type="text/plain; charset=utf-8")
    from apps.assessment.models import AssessmentEvent, ExperimentSession
    preview = {
        "sessions": ExperimentSession.objects.filter(version__experiment__course=course).count(),
        "events": AssessmentEvent.objects.filter(session__version__experiment__course=course).count(),
    }
    DataPurgeRequest.objects.create(
        scope_type="course",
        scope_definition={"course_id": course.pk},
        reason_code=request.POST.get("reason_code", "administrative_correction"),
        requested_by=request.user,
        preview_counts=preview,
    )
    return redirect("courses:settings", course.pk)


@role_required("teacher", "admin")
@require_POST
def purge_request_execute(request, course_pk, purge_request_id):
    course = get_accessible_course(request.user, course_pk)
    require_course_manager(course, request.user)
    purge = get_object_or_404(DataPurgeRequest, purge_request_id=purge_request_id, scope_definition__course_id=course.pk)
    execute_purge_request(purge, executed_by=request.user)
    log_action(request, "data_purge.execute", course, {"purge_request_id": str(purge.purge_request_id)})
    return redirect("courses:settings", course.pk)


@role_required("teacher", "admin")
def export_job_download(request, course_pk, job_id):
    course = get_accessible_course(request.user, course_pk)
    job = get_object_or_404(ExportJob, course=course, job_id=job_id, status=ExportJob.Status.SUCCEEDED)
    if job.kind == ExportJob.Kind.IDENTITY and not (
        request.user.is_superuser or request.user.role == User.Role.ADMIN or course.owner_id == request.user.id
    ):
        return HttpResponse("无权访问身份任务", status=403)
    if not job.artifact_path or not os.path.exists(job.artifact_path):
        return HttpResponse("导出文件不存在，请重新产出", status=404, content_type="text/plain; charset=utf-8")
    if job.kind == ExportJob.Kind.IDENTITY:
        log_action(request, "identity.export_job_download", course, {"job_id": str(job.job_id)})
    return FileResponse(open(job.artifact_path, "rb"), as_attachment=True, filename=f"{job.package}-{job.job_id}.zip")


@role_required("teacher", "admin")
@require_POST
def export_job_delete(request, course_pk, job_id):
    course = get_accessible_course(request.user, course_pk)
    job = get_object_or_404(ExportJob, course=course, job_id=job_id)
    if job.kind == ExportJob.Kind.IDENTITY and not (
        request.user.is_superuser or request.user.role == User.Role.ADMIN or course.owner_id == request.user.id
    ):
        return HttpResponse("无权删除身份导出", status=403, content_type="text/plain; charset=utf-8")
    if job.artifact_path and os.path.exists(job.artifact_path):
        artifact_dir = os.path.dirname(job.artifact_path)
        os.remove(job.artifact_path)
        if artifact_dir and os.path.isdir(artifact_dir) and not os.listdir(artifact_dir):
            os.rmdir(artifact_dir)
    log_action(request, "analytics.export_job_delete", course, {"job_id": str(job.job_id), "package": job.package})
    job.delete()
    return redirect("courses:data", course.pk)


def _prepare_export_job(*, course, snapshot, created_by, experiment=None, research_study=None,
                        kind=ExportJob.Kind.RESEARCH, package="complete", selected_tables=None,
                        selected_fields=None):
    lookup = {
        "course": course,
        "experiment": experiment,
        "research_study": research_study,
        "kind": kind,
        "package": package,
    }
    job = ExportJob.objects.filter(**lookup).order_by("-created_at").first()
    if job is None:
        job = ExportJob.objects.create(
            **lookup,
            snapshot=snapshot,
            created_by=created_by,
            selected_tables=selected_tables or [],
            selected_fields=selected_fields or [],
        )
    else:
        if job.artifact_path and os.path.exists(job.artifact_path):
            os.remove(job.artifact_path)
        job.snapshot = snapshot
        job.created_by = created_by
        job.selected_tables = selected_tables or []
        job.selected_fields = selected_fields or []
        job.status = ExportJob.Status.RUNNING
        job.started_at = timezone.now()
        job.finished_at = None
        job.artifact_path = ""
        job.artifact_sha256 = ""
        job.progress_stage = "snapshot"
        job.save(update_fields=[
            "snapshot", "created_by", "selected_tables", "selected_fields", "status",
            "started_at", "finished_at", "artifact_path", "artifact_sha256", "progress_stage",
        ])
    if job.status != ExportJob.Status.RUNNING:
        job.status = ExportJob.Status.RUNNING
        job.started_at = timezone.now()
        job.progress_stage = "snapshot"
        job.save(update_fields=["status", "started_at", "progress_stage"])
    return job

def _anon(student_id, experiment_id):
    return hashlib.sha256(f"kt:{experiment_id}:{student_id}".encode()).hexdigest()[:16]

def _csv(name, headers, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response.write("﻿")
    writer = csv.writer(response); writer.writerow(headers)
    for row in rows: writer.writerow(row)
    return response


def _attachment(response, ascii_name, unicode_name):
    response["Content-Disposition"] = (
        f"attachment; filename={ascii_name}; filename*=UTF-8''{quote(unicode_name)}"
    )
    return response

@role_required("teacher", "admin")
def kt_export(request, experiment_id):
    experiment = _experiment(request, experiment_id)
    attempts = Attempt.objects.filter(session__version__experiment=experiment).select_related("session__student", "session__version", "assignment__snapshot").order_by("session_id", "submission_sequence_index", "assignment__assigned_sequence_index")
    headers = ["anonymous_student_id", "experiment_id", "experiment_version", "session_id", "question_code", "primary_kc", "all_kcs", "question_type", "assigned_sequence_index", "first_open_sequence_index", "submission_sequence_index", "first_opened_at", "last_opened_at", "submitted_at", "visit_count", "first_to_submit_duration_ms", "last_visit_to_submission_ms", "final_answer", "is_correct", "score", "status", "teacher_difficulty", "empirical_difficulty", "class_name"]
    rows = []
    for a in attempts:
        s = a.assignment.snapshot
        rows.append([_anon(a.session.student_id, experiment.id), experiment.id, a.session.version.version_number, a.session_id, s.code, s.primary_kc, ";".join(s.all_kcs), s.question_type, a.assignment.assigned_sequence_index, a.first_open_sequence_index, a.submission_sequence_index, a.first_opened_at, a.last_opened_at, a.submitted_at, a.visit_count, a.first_to_submit_duration_ms, a.last_visit_to_submission_ms, a.final_answer, a.is_correct, a.score, a.status, s.teacher_difficulty, s.empirical_difficulty, a.session.class_snapshot_name])
    return _csv(f"experiment_{experiment.id}_kt.csv", headers, rows)

@role_required("teacher", "admin")
def navigation_export(request, experiment_id):
    experiment = _experiment(request, experiment_id)
    events = QuestionVisitEvent.objects.filter(session__version__experiment=experiment).select_related("session__student", "attempt__assignment__snapshot", "from_assignment__snapshot").order_by("session_id", "event_sequence")
    headers = ["anonymous_student_id", "experiment_id", "session_id", "event_sequence", "from_question", "to_question", "visit_index", "entry_reason", "client_occurred_at", "server_received_at"]
    rows = [[_anon(e.session.student_id, experiment.id), experiment.id, e.session_id, e.event_sequence, e.from_assignment.snapshot.code if e.from_assignment else "", e.attempt.assignment.snapshot.code, e.visit_index, e.entry_reason, e.client_occurred_at, e.server_received_at] for e in events]
    return _csv(f"experiment_{experiment.id}_navigation.csv", headers, rows)

@role_required("teacher", "admin")
def identity_export(request, experiment_id):
    experiment = _identified_experiment(request, experiment_id)
    students = experiment.students.select_related("class_group")
    headers = ["anonymous_student_id", "student_number", "student_name", "class_name"]
    rows = [[_anon(s.id, experiment.id), s.student_number, s.shown_name, str(s.class_group or "")] for s in students]
    return _csv(f"experiment_{experiment.id}_identity.csv", headers, rows)


def _literal_cell(sheet, value):
    if isinstance(value, str):
        cell = WriteOnlyCell(sheet, value=value)
        cell.data_type = "s"
        return cell
    return value


@role_required("teacher", "admin")
@require_POST
def identified_export(request, experiment_id):
    experiment = _identified_experiment(request, experiment_id)
    requested = request.POST.getlist("fields")
    if not requested or any(key not in FIELD_LABELS for key in requested):
        return HttpResponse("请选择有效的导出字段", status=400, content_type="text/plain; charset=utf-8")
    requested_set = set(requested)
    selected = [key for key in FIELD_LABELS if key in requested_set]
    attempts = (
        Attempt.objects.filter(session__version__experiment=experiment)
        .select_related(
            "session__student",
            "session__version",
            "assignment__snapshot",
        )
        .order_by(
            "session__student__student_number",
            F("assignment__snapshot__course_sequence").asc(nulls_last=True),
            "assignment__assigned_sequence_index",
            "pk",
        )
    )
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("KT数据")
    sheet.append([_literal_cell(sheet, FIELD_LABELS[key]) for key in selected])
    row_count = 0
    for attempt in attempts.iterator(chunk_size=1000):
        values = field_values(attempt)
        sheet.append([_literal_cell(sheet, values[key]) for key in selected])
        row_count += 1
    stream = BytesIO()
    workbook.save(stream)
    log_action(
        request,
        "experiment.identified_export",
        experiment,
        {"fields": selected, "row_count": row_count},
    )
    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return _attachment(
        response,
        f"experiment_{experiment.pk}_identified.xlsx",
        f"实验_{experiment.pk}_实名KT数据.xlsx",
    )


@role_required("teacher", "admin")
@require_POST
def identified_navigation_export(request, experiment_id):
    experiment = _identified_experiment(request, experiment_id)
    events = (
        QuestionVisitEvent.objects.filter(session__version__experiment=experiment)
        .select_related(
            "session__student",
            "attempt__assignment__snapshot",
            "from_assignment__snapshot",
        )
        .order_by("session_id", "event_sequence")
    )
    headers = [
        "student_number", "student_name", "class_name", "experiment_id", "session_id",
        "event_sequence", "from_question", "to_question", "visit_index", "entry_reason",
        "client_occurred_at", "server_received_at",
    ]
    rows = [
        [
            event.session.student.student_number,
            event.session.student.shown_name,
            event.session.class_snapshot_name,
            experiment.pk,
            event.session_id,
            event.event_sequence,
            event.from_assignment.snapshot.code if event.from_assignment else "",
            event.attempt.assignment.snapshot.code,
            event.visit_index,
            event.entry_reason,
            event.client_occurred_at,
            event.server_received_at,
        ]
        for event in events
    ]
    log_action(
        request,
        "experiment.identified_navigation_export",
        experiment,
        {"row_count": len(rows)},
    )
    response = _csv(f"experiment_{experiment.pk}_navigation_identified.csv", headers, rows)
    return _attachment(
        response,
        f"experiment_{experiment.pk}_navigation_identified.csv",
        f"实验_{experiment.pk}_实名导航事件.csv",
    )
