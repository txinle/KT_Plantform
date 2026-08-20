import csv
import hashlib
from io import BytesIO
from urllib.parse import quote

from django.http import HttpResponse
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
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

def _experiment(request, pk):
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        qs = Experiment.objects.all()
    else:
        qs = Experiment.objects.filter(
            Q(course__in=accessible_courses(request.user))
            | Q(course__isnull=True, owner=request.user)
        )
    return get_object_or_404(qs, pk=pk)

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
    experiment = _experiment(request, experiment_id)
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
    experiment = _experiment(request, experiment_id)
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
    experiment = _experiment(request, experiment_id)
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
