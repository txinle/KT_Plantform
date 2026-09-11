"""Lightweight Advanced Data Center reads over versioned session projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from math import ceil
from urllib.parse import urlencode

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import CourseEnrollment, User
from apps.assessment.models import ExperimentSession
from apps.question_bank.models import Concept, Question
from apps.question_bank.services.question_kc_mapping import (
    project_question_kc_mapping,
    question_kc_mapping_prefetch,
)

from .models import (
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
    ResearchSessionVisit,
)
from .research_contract import FILES
from .research_dataset import TABLES, scalar, scope_label
from .research_materialization import PROJECTION_VERSION
from .research_visits import research_time


PAGE_SIZE = 20
CURSOR_SALT = "analytics.advanced-behavior.v1"
HEADERS = {item["key"]: item["headers"] for item in FILES}
SESSION_HEADERS = "sessionID 学号 姓名 课程 实验名称 开始时间 结束时间 时长(分钟)".split()
QUESTION_HEADERS = "题号 题干 选项 答案 解析 难度".split()


def _page_number(value):
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _page_payload(number, total):
    total_pages = max(1, ceil(total / PAGE_SIZE))
    number = min(number, total_pages)
    start = (number - 1) * PAGE_SIZE
    return {
        "number": number,
        "size": PAGE_SIZE,
        "total_items": total,
        "total_pages": total_pages,
        "start": start + 1 if total else 0,
        "end": min(start + PAGE_SIZE, total),
    }


def _columns(headers):
    return [{"name": value, "description": value} for value in headers]


def _scope_signature(scope, params):
    body = {
        "scope": scope.serialize(),
        "question_ref": params.get("question_ref") or "",
        "visit": params.get("visit") or "",
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _decode_cursor(value, *, signature):
    if not value:
        return None
    try:
        payload = signing.loads(value, salt=CURSOR_SALT)
    except signing.BadSignature as error:
        raise ValidationError({"cursor": ["行为记录游标无效，请从第一页重新打开。"]}) from error
    if payload.get("scope") != signature:
        raise ValidationError({"cursor": ["行为记录游标与当前数据范围不一致。"]})
    return payload


def _encode_cursor(
    *, cutoff, signature, direction, session_id, ordinal, page, session_ids
):
    return signing.dumps(
        {
            "cutoff": cutoff.isoformat(),
            "scope": signature,
            "direction": direction,
            "session_id": session_id,
            "ordinal": ordinal,
            "page": page,
            "session_ids": session_ids,
        },
        salt=CURSOR_SALT,
        compress=True,
    )


def _session_reference(session):
    from zoneinfo import ZoneInfo

    return f"S{session.started_at.astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}_{session.pk:04d}"


def _fallback_session_summary(session):
    end = session.authoritative_terminal_at or session.completed_at
    return {
        "session_ref": _session_reference(session),
        "student_id": session.student_id,
        "student_number": session.student.student_number or "",
        "name": session.student.shown_name,
        "course_name": session.version.experiment.course.name,
        "experiment_id": session.version.experiment_id,
        "experiment_name": session.version.experiment.name,
        "class_name": session.class_snapshot_name,
        "status_label": session.get_status_display(),
        "started_at": research_time(session.started_at),
        "ended_at": research_time(end),
        "submitted_at": research_time(session.completed_at),
        "duration_minutes": (
            round((end - session.started_at).total_seconds() / 60, 2) if end else None
        ),
        "question_count": 0,
        "behavior_count": 0,
    }


def _fallback_session_row(summary):
    return [
        summary[key]
        for key in (
            "session_ref",
            "student_number",
            "name",
            "course_name",
            "experiment_name",
            "started_at",
            "ended_at",
            "duration_minutes",
        )
    ]


def _session_queryset(scope):
    return (
        scope.sessions()
        .exclude(status=ExperimentSession.Status.INVALIDATED)
        .select_related("student", "version__experiment__course", "class_snapshot")
        .order_by("pk")
    )


def _current_materializations(session_queryset):
    return list(
        ResearchSessionMaterialization.objects.filter(
            session__in=session_queryset,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
        )
        .select_related("session")
        .order_by("session_id")
    )


def _row_counts(current):
    return {
        "responses": sum(int(item.row_counts.get("responses", 0)) for item in current),
        "behavior_events": sum(int(item.row_counts.get("behavior", 0)) for item in current),
    }


def _table_catalog(course, session_queryset, current):
    counts = _row_counts(current)
    question_count = Question.objects.filter(bank__course=course).count()
    totals = {
        "sessions": session_queryset.count(),
        "questions": question_count,
        "knowledge_points": Concept.objects.filter(course=course).count(),
        "question_kc_mapping": question_count,
        **counts,
    }
    return [
        {
            "key": key,
            "label": label,
            "path": f"{label}.csv",
            "description": description,
            "total_items": totals[key],
        }
        for key, (label, description) in TABLES.items()
    ]


def _library_page(course, key, page):
    if key == "questions":
        queryset = (
            Question.objects.filter(bank__course=course)
            .select_related("course_identity")
            .prefetch_related("options")
            .order_by("course_identity__course_sequence", "pk")
        )
        total = queryset.count()
        page = _page_payload(page, total)["number"]
        start = (page - 1) * PAGE_SIZE
        rows = [
            [
                f"Q{question.course_identity.course_sequence:03d}",
                question.text,
                scalar({option.key: option.text for option in question.options.all()}),
                scalar(question.correct_answer),
                question.explanation,
                float(question.teacher_difficulty)
                if question.teacher_difficulty is not None
                else "",
            ]
            for question in queryset[start : start + PAGE_SIZE]
        ]
        return QUESTION_HEADERS, rows, total, page
    if key == "knowledge_points":
        queryset = Concept.objects.filter(course=course).order_by("code", "pk")
        total = queryset.count()
        page = _page_payload(page, total)["number"]
        start = (page - 1) * PAGE_SIZE
        rows = [[course.name, item.code, item.name] for item in queryset[start : start + PAGE_SIZE]]
        return HEADERS["course_knowledge"], rows, total, page
    queryset = (
        Question.objects.filter(bank__course=course)
        .select_related("course_identity")
        .prefetch_related(question_kc_mapping_prefetch())
        .order_by("course_identity__course_sequence", "pk")
    )
    total = queryset.count()
    page = _page_payload(page, total)["number"]
    start = (page - 1) * PAGE_SIZE
    rows = []
    for question in queryset[start : start + PAGE_SIZE]:
        nodes = project_question_kc_mapping(question)["nodes"]
        rows.append(
            [
                course.name,
                f"Q{question.course_identity.course_sequence:03d}",
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 1),
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 2),
                ";".join(
                    f"{node['parent_kc_code']}>{node['kc_code']}"
                    for node in nodes
                    if node["depth"] == 3
                ),
            ]
        )
    return HEADERS["course_mapping"], rows, total, page


def _standard_preview(course, scope, session_queryset, current, key, params):
    page = _page_number(params.get("page"))
    current_by_session = {item.session_id: item for item in current}
    if key == "sessions":
        total = session_queryset.count()
        page = _page_payload(page, total)["number"]
        start = (page - 1) * PAGE_SIZE
        sessions = list(session_queryset[start : start + PAGE_SIZE])
        rows = []
        for session in sessions:
            item = current_by_session.get(session.pk)
            summary = item.session_summary if item else _fallback_session_summary(session)
            rows.append(item.session_row if item else _fallback_session_row(summary))
        headers = SESSION_HEADERS
    elif key in {"questions", "knowledge_points", "question_kc_mapping"}:
        headers, rows, total, page = _library_page(course, key, page)
    else:
        queryset = ResearchSessionResponse.objects.filter(
            materialization__in=current
        ).order_by("materialization__session_id", "ordinal")
        total = queryset.count()
        page = _page_payload(page, total)["number"]
        start = (page - 1) * PAGE_SIZE
        rows = list(queryset.values_list("row", flat=True)[start : start + PAGE_SIZE])
        headers = HEADERS["responses"]
    return {
        "columns": _columns(headers),
        "rows": rows,
        "page": _page_payload(page, total),
        "data_status": "observed" if total else "no_observation",
    }


def _fixed_materializations(session_queryset, cutoff):
    queryset = ResearchSessionMaterialization.objects.filter(
        session__in=session_queryset,
        status=ResearchSessionMaterialization.Status.SUCCEEDED,
        activated_at__lte=cutoff,
    ).filter(Q(superseded_at__isnull=True) | Q(superseded_at__gt=cutoff))
    return list(queryset.order_by("session_id"))


@transaction.atomic
def _behavior_page(scope, session_queryset, params):
    signature = _scope_signature(scope, params)
    decoded = _decode_cursor(params.get("cursor"), signature=signature)
    if decoded:
        cutoff = datetime.fromisoformat(decoded["cutoff"])
        session_ids = [int(value) for value in decoded.get("session_ids", [])]
        materializations = _fixed_materializations(
            session_queryset.filter(pk__in=session_ids), cutoff
        )
    else:
        session_ids = list(
            session_queryset.select_for_update(of=("self",))
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        materializations = list(
            ResearchSessionMaterialization.objects.select_for_update()
            .filter(
                session_id__in=session_ids,
                status=ResearchSessionMaterialization.Status.SUCCEEDED,
                is_current=True,
            )
            .order_by("session_id")
        )
        cutoff = timezone.now()
    result_version = hashlib.sha256(
        ":".join(str(item.pk) for item in materializations).encode()
    ).hexdigest()[:24]
    queryset = ResearchSessionBehavior.objects.filter(
        materialization__in=materializations
    ).select_related("materialization")
    if params.get("question_ref"):
        queryset = queryset.filter(question_ref=params["question_ref"])
    if params.get("visit"):
        queryset = queryset.filter(visit_key=params["visit"])
    total = queryset.count()
    direction = decoded.get("direction") if decoded else "next"
    if decoded:
        boundary = Q(materialization__session_id__gt=decoded["session_id"]) | Q(
            materialization__session_id=decoded["session_id"],
            ordinal__gt=decoded["ordinal"],
        )
        if direction == "previous":
            boundary = Q(materialization__session_id__lt=decoded["session_id"]) | Q(
                materialization__session_id=decoded["session_id"],
                ordinal__lt=decoded["ordinal"],
            )
        queryset = queryset.filter(boundary)
    ordering = ("-materialization__session_id", "-ordinal") if direction == "previous" else (
        "materialization__session_id", "ordinal"
    )
    page_rows = list(queryset.order_by(*ordering)[:PAGE_SIZE])
    if direction == "previous":
        page_rows.reverse()
    number = max(1, int(decoded.get("page", 1))) if decoded else 1
    previous = None
    next_value = None
    if page_rows:
        first = page_rows[0]
        last = page_rows[-1]
        base = ResearchSessionBehavior.objects.filter(materialization__in=materializations)
        if params.get("question_ref"):
            base = base.filter(question_ref=params["question_ref"])
        if params.get("visit"):
            base = base.filter(visit_key=params["visit"])
        has_previous = base.filter(
            Q(materialization__session_id__lt=first.materialization.session_id)
            | Q(
                materialization__session_id=first.materialization.session_id,
                ordinal__lt=first.ordinal,
            )
        ).exists()
        has_next = base.filter(
            Q(materialization__session_id__gt=last.materialization.session_id)
            | Q(
                materialization__session_id=last.materialization.session_id,
                ordinal__gt=last.ordinal,
            )
        ).exists()
        if has_previous:
            previous = _encode_cursor(
                cutoff=cutoff,
                signature=signature,
                direction="previous",
                session_id=first.materialization.session_id,
                ordinal=first.ordinal,
                page=max(1, number - 1),
                session_ids=session_ids,
            )
        if has_next:
            next_value = _encode_cursor(
                cutoff=cutoff,
                signature=signature,
                direction="next",
                session_id=last.materialization.session_id,
                ordinal=last.ordinal,
                page=number + 1,
                session_ids=session_ids,
            )
    preview = {
        "columns": _columns(HEADERS["behavior_events"]),
        "rows": [item.row for item in page_rows],
        "page": _page_payload(number, total),
        "cursor": {
            "previous": previous,
            "next": next_value,
            "result_version": result_version,
        },
        "data_status": "observed" if total else "no_observation",
    }
    payloads = [
        {key: value for key, value in item.payload.items() if key != "session_id"}
        for item in page_rows
    ]
    return preview, payloads


def _selected_session(session_queryset, scope):
    ordered = session_queryset.order_by("-started_at", "-pk")
    selected = None
    if scope.session_id:
        selected = ordered.filter(pk=scope.session_id).first()
    elif scope.session_ref:
        try:
            referenced_id = int(scope.session_ref.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            referenced_id = None
        if referenced_id is not None:
            candidate = ordered.filter(pk=referenced_id).first()
            if candidate and _session_reference(candidate) == scope.session_ref:
                selected = candidate
    if selected is not None:
        sessions = [selected, *list(ordered.exclude(pk=selected.pk)[:49])]
        return selected, sessions
    sessions = list(ordered[:50])
    return (sessions[0] if sessions else None), sessions


def _preparation(session, current=None):
    if session is None:
        return {
            "state": "ready",
            "message": "当前范围没有会话。",
            "updated_at": None,
            "result_version": None,
            "projection_version": PROJECTION_VERSION,
            "stale": False,
        }
    if session.status == ExperimentSession.Status.IN_PROGRESS:
        state, message = "in_progress", "学生正在作答，结束后开始整理研究数据。"
    elif session.status == ExperimentSession.Status.INVALIDATED:
        state, message = "invalidated", "该会话已作废，不进入研究数据派生结果。"
    elif current is None:
        latest = session.research_materializations.order_by("-created_at").first()
        if latest and latest.status == latest.Status.FAILED:
            state, message = "failed", "研究数据整理失败，系统将保留错误信息供重试。"
        else:
            state, message = "preparing", "会话已结束，研究数据正在后台整理。"
    else:
        active = session.research_materializations.filter(
            status__in=(current.Status.QUEUED, current.Status.RUNNING)
        ).exists()
        state = "updating" if active else "ready"
        message = "已有结果可用，更新版本正在后台整理。" if active else "研究数据已整理完成。"
    return {
        "state": state,
        "message": message,
        "updated_at": current.finished_at if current else None,
        "result_version": str(current.pk) if current else None,
        "projection_version": current.projection_version if current else PROJECTION_VERSION,
        "stale": state == "updating",
    }


def _student_summary(course, student, sessions):
    enrollment = (
        CourseEnrollment.objects.filter(course=course, student=student)
        .select_related("class_group")
        .first()
    )
    class_name = (
        enrollment.class_group.name
        if enrollment and enrollment.class_group_id
        else (sessions[0].class_snapshot_name if sessions else "")
    )
    return {
        "id": student.pk,
        "student_number": student.student_number or "",
        "name": student.shown_name,
        "class_name": class_name,
        "course_name": course.name,
        "experiment_count": len({item.version.experiment_id for item in sessions}),
    }


def materialized_advanced_payload(course, scope, params):
    view = params.get("view") if params.get("view") in {
        "overview", "student", "session", "question", "timeline"
    } else "overview"
    selected_key = params.get("table") or ("sessions" if view == "overview" else "responses")
    if selected_key == "attempts":
        selected_key = "responses"
    if view in {"question", "timeline"}:
        selected_key = "behavior_events"
    elif view in {"student", "session"}:
        selected_key = "responses"

    read_scope = (
        replace(scope, session_id=None, session_ref=None)
        if view == "student"
        else scope
    )
    session_queryset = (
        _session_queryset(read_scope)
        if scope.is_complete
        else ExperimentSession.objects.none()
    )
    current = _current_materializations(session_queryset) if scope.is_complete else []
    current_by_session = {item.session_id: item for item in current}
    tables = _table_catalog(course, session_queryset, current)
    selected = next((item for item in tables if item["key"] == selected_key), tables[0])
    selected_key = selected["key"]
    preview = None
    behavior_payloads = []
    if scope.is_complete and view == "overview":
        if selected_key == "behavior_events":
            preview, behavior_payloads = _behavior_page(scope, session_queryset, params)
        else:
            preview = _standard_preview(
                course, scope, session_queryset, current, selected_key, params
            )

    selected_session, visible_sessions = _selected_session(session_queryset, scope)
    selected_current = current_by_session.get(selected_session.pk) if selected_session else None
    session_summaries = [
        (
            current_by_session[item.pk].session_summary
            if item.pk in current_by_session
            else _fallback_session_summary(item)
        )
        for item in visible_sessions
    ]
    student = None
    if scope.student_id:
        selected_student = User.objects.filter(pk=scope.student_id).first()
        if selected_student:
            student = _student_summary(course, selected_student, visible_sessions)

    responses = []
    visits = []
    question = None
    selected_visit = None
    if selected_current and view in {"session", "question", "timeline"}:
        response_rows = list(selected_current.response_rows.order_by("ordinal")[:100])
        responses = [
            {key: value for key, value in item.payload.items() if key != "session_id"}
            for item in response_rows
        ]
        question_ref = params.get("question_ref") or (
            responses[0].get("question_ref") if responses else None
        )
        question = next(
            (item for item in responses if item.get("question_ref") == question_ref),
            None,
        )
        if view in {"question", "timeline"}:
            visit_rows = selected_current.visit_rows.order_by("ordinal")
            if view == "question" and question_ref:
                visit_rows = visit_rows.filter(question_ref=question_ref)
            visits = [
                {key: value for key, value in item.payload.items() if key != "session_id"}
                for item in visit_rows[:500]
            ]
            selected_visit = params.get("visit") or (
                visits[0].get("key") if view == "timeline" and visits else None
            )
            behavior_params = params.copy()
            if view == "question" and question_ref:
                behavior_params["question_ref"] = question_ref
            if view == "timeline" and selected_visit:
                behavior_params["visit"] = selected_visit
            preview, behavior_payloads = _behavior_page(
                scope, session_queryset.filter(pk=selected_session.pk), behavior_params
            )

    workspace = {
        "students": [],
        "student": student,
        "sessions": session_summaries if view == "student" else (
            session_summaries[:1] if selected_session else []
        ),
        "session": (
            current_by_session[selected_session.pk].session_summary
            if selected_session and selected_session.pk in current_by_session
            else (_fallback_session_summary(selected_session) if selected_session else None)
        ),
        "responses": responses,
        "visits": visits,
        "behavior": behavior_payloads,
        "question": question,
        "selected_visit": selected_visit,
    }
    session_options = [
        {
            "value": _session_reference(item),
            "label": f"{_session_reference(item)} · {item.student.student_number or ''} {item.student.shown_name}",
        }
        for item in visible_sessions[:20]
    ] if scope.student_id else []
    experiment = course.experiments.filter(pk=scope.experiment_id).first() if scope.experiment_id else None
    generated_at = max(
        (item.finished_at for item in current if item.finished_at), default=timezone.now()
    )
    return {
        "course": {
            "id": course.pk,
            "name": course.name,
            "code": course.code,
            "term": course.term,
            "status": course.status,
        },
        "scope": {
            "values": scope.serialize(),
            "query": urlencode(
                {
                    key: ",".join(map(str, value)) if isinstance(value, list) else value
                    for key, value in scope.serialize().items()
                }
            ),
            "display": scope_label(scope),
            "is_single_version": scope.is_single_version,
            "is_complete": scope.is_complete,
        },
        "search": scope.search or "",
        "tables": tables,
        "selected_table": selected,
        "selected_experiment": (
            {"id": experiment.pk, "name": experiment.name, "versions": []}
            if experiment else None
        ),
        "session_options": session_options,
        "generated_at": generated_at,
        "preview": preview,
        "view": view,
        "workspace": workspace,
        "preparation": _preparation(selected_session, selected_current),
    }


def materialized_student_search(course, scope, query):
    base = replace(
        scope,
        student_id=None,
        session_id=None,
        session_ref=None,
        search=None,
    )
    sessions = _session_queryset(base)
    students = User.objects.filter(
        role=User.Role.STUDENT, assessment_sessions__in=sessions
    )
    text = str(query or "").strip()[:100]
    if text:
        students = students.filter(
            Q(display_name__icontains=text)
            | Q(student_number__icontains=text)
            | Q(username__icontains=text)
        )
    rows = students.order_by("display_name", "student_number", "pk").distinct()[:20]
    return {
        "items": [
            {
                "id": item.pk,
                "student_number": item.student_number or "",
                "name": item.shown_name,
            }
            for item in rows
        ],
        "limit": 20,
    }
