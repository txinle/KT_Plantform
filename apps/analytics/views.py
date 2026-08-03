import csv
import hashlib
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import role_required
from apps.assessment.models import Attempt, QuestionVisitEvent
from apps.experiments.models import Experiment

def _experiment(request, pk):
    qs = Experiment.objects.all() if request.user.is_superuser else Experiment.objects.filter(owner=request.user)
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
