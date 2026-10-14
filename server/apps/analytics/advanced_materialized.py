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
from django.contrib.postgres.aggregates import StringAgg
from django.db.models import BigIntegerField, Count, Exists, F, Max, Min, OuterRef, Q, Subquery, Sum, TextField
from django.db.models.functions import Cast, Coalesce, MD5
from django.db.models.fields.json import KeyTextTransform
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
from .research_materialization import PROJECTION_VERSION, lock_research_activation
from .research_visits import research_time
from .runtime_pressure import runtime_pressure


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
    *, cutoff, signature, direction, session_id, ordinal, page
):
    return signing.dumps(
        {
            "cutoff": cutoff.isoformat(),
            "scope": signature,
            "direction": direction,
            "session_id": session_id,
            "ordinal": ordinal,
            "page": page,
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
        .filter(
            status__in=(
                ExperimentSession.Status.COMPLETED,
                ExperimentSession.Status.EXPIRED,
            )
        )
        .select_related("student", "version")
        .only("pk", "started_at", "student_id", "class_snapshot_name", "version__experiment_id",
              "student__student_number", "student__display_name", "student__first_name",
              "student__last_name", "student__username")
        .order_by("pk")
    )


def _current_materializations(session_queryset):
    return ResearchSessionMaterialization.objects.filter(
        session__in=session_queryset,
        status=ResearchSessionMaterialization.Status.SUCCEEDED,
        is_current=True,
        projection_version=PROJECTION_VERSION,
    ).order_by("session_id")


def _table_catalog(course, session_queryset, current):
    question_count = Question.objects.filter(bank__course=course).count()
    row_counts = current.aggregate(
        responses=Coalesce(Sum(Cast(KeyTextTransform("responses", "row_counts"), BigIntegerField())), 0),
        behavior=Coalesce(Sum(Cast(KeyTextTransform("behavior", "row_counts"), BigIntegerField())), 0),
    )
    totals = {
        "sessions": current.count(),
        "questions": question_count,
        "knowledge_points": Concept.objects.filter(course=course).count(),
        "question_kc_mapping": question_count,
        "responses": row_counts["responses"],
        "behavior_events": row_counts["behavior"],
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
    if key == "sessions":
        total = session_queryset.count()
        page = _page_payload(page, total)["number"]
        start = (page - 1) * PAGE_SIZE
        rows = list(current.values_list("session_row", flat=True)[start : start + PAGE_SIZE])
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
    return ResearchSessionMaterialization.objects.filter(
        session__in=session_queryset,
        status=ResearchSessionMaterialization.Status.SUCCEEDED,
        projection_version=PROJECTION_VERSION,
        activated_at__lte=cutoff,
    ).filter(Q(superseded_at__isnull=True) | Q(superseded_at__gt=cutoff)).order_by("session_id")


@transaction.atomic
def _behavior_page(scope, session_queryset, params):
    signature = _scope_signature(scope, params)
    decoded = _decode_cursor(params.get("cursor"), signature=signature)
    if decoded:
        cutoff = datetime.fromisoformat(decoded["cutoff"])
    else:
        lock_research_activation()
        cutoff = timezone.now()
    materializations = _fixed_materializations(session_queryset, cutoff)
    result_version = hashlib.sha256(f"{signature}:{cutoff.isoformat()}:{PROJECTION_VERSION}".encode()).hexdigest()[:24]
    base = ResearchSessionBehavior.objects.filter(materialization__in=materializations)
    if params.get("question_ref"):
        base = base.filter(question_ref=params["question_ref"])
    if params.get("visit"):
        base = base.filter(visit_key=params["visit"])
    total = base.count()
    direction = decoded.get("direction") if decoded else "next"
    queryset = base
    if decoded:
        comparison = "lt" if direction == "previous" else "gt"
        queryset = queryset.filter(
            Q(**{f"materialization__session_id__{comparison}": decoded["session_id"]})
            | Q(materialization__session_id=decoded["session_id"], **{f"ordinal__{comparison}": decoded["ordinal"]})
        )
    ordering = ("-materialization__session_id", "-ordinal") if direction == "previous" else (
        "materialization__session_id", "ordinal"
    )
    page_rows = list(queryset.order_by(*ordering).values(
        "row", "payload", "ordinal", "materialization__session_id"
    )[:PAGE_SIZE])
    if direction == "previous":
        page_rows.reverse()
    number = max(1, int(decoded.get("page", 1))) if decoded else 1
    previous = None
    next_value = None
    if page_rows:
        first, last = page_rows[0], page_rows[-1]
        for row, comparison in ((first, "lt"), (last, "gt")):
            session_id = row["materialization__session_id"]
            exists = base.filter(
                Q(**{f"materialization__session_id__{comparison}": session_id})
                | Q(materialization__session_id=session_id, **{f"ordinal__{comparison}": row["ordinal"]})
            ).exists()
            if exists:
                value = _encode_cursor(
                    cutoff=cutoff, signature=signature,
                    direction="previous" if comparison == "lt" else "next",
                    session_id=session_id, ordinal=row["ordinal"],
                    page=max(1, number - 1) if comparison == "lt" else number + 1,
                )
                if comparison == "lt":
                    previous = value
                else:
                    next_value = value
    preview = {
        "columns": _columns(HEADERS["behavior_events"]),
        "rows": [item["row"] for item in page_rows],
        "page": _page_payload(number, total),
        "cursor": {"previous": previous, "next": next_value, "result_version": result_version},
        "data_status": "observed" if total else "no_observation",
    }
    payloads = [
        {key: value for key, value in item["payload"].items() if key != "session_id"}
        for item in page_rows
    ]
    return preview, payloads


def _selected_session(session_queryset, scope, limit=50):
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
        sessions = [selected, *list(ordered.exclude(pk=selected.pk)[:limit - 1])]
        return selected, sessions
    sessions = list(ordered[:limit])
    return (sessions[0] if sessions else None), sessions


def _preparation(session_queryset, current):
    latest = ResearchSessionMaterialization.objects.filter(
        session_id=OuterRef("pk"), projection_version=PROJECTION_VERSION,
        source_revision=OuterRef("research_source_revision"),
    ).order_by("-created_at", "-pk")
    sessions = session_queryset.annotate(
        has_current=Exists(current.filter(session_id=OuterRef("pk"))),
        latest_materialization_status=Subquery(latest.values("status")[:1]),
        latest_materialization_created_at=Subquery(latest.values("created_at")[:1]),
        source_stale=Exists(current.filter(
            session_id=OuterRef("pk"), source_revision__lt=F("session__research_source_revision")
        )),
    )
    failed = Q(latest_materialization_status=ResearchSessionMaterialization.Status.FAILED)
    pending = Q(has_current=False) & (Q(latest_materialization_status__isnull=True) | ~failed)
    updating = Q(has_current=True) & (
        Q(source_stale=True) | Q(latest_materialization_status__in=(
            ResearchSessionMaterialization.Status.QUEUED, ResearchSessionMaterialization.Status.RUNNING,
        ))
    )
    totals = sessions.aggregate(
        total=Count("pk"), ready_count=Count("pk", filter=Q(has_current=True)),
        failed_count=Count("pk", filter=failed), pending_count=Count("pk", filter=pending),
        updating_count=Count("pk", filter=updating), stale_count=Count("pk", filter=Q(source_stale=True)),
        oldest_pending_at=Min("latest_materialization_created_at", filter=pending),
    )
    failed_count, pending_count, updating_count = (
        totals["failed_count"], totals["pending_count"], totals["updating_count"]
    )
    oldest_pending_at = totals["oldest_pending_at"]
    oldest_pending_seconds = max(0, int((timezone.now() - oldest_pending_at).total_seconds())) if oldest_pending_at else None
    if failed_count:
        state = "failed"
        message = f"{failed_count} 名已结束学生的数据整理失败；已有兼容版本仍可查看，缺失的数据暂不显示。"
    elif pending_count:
        state = "preparing"
        wait_note = f"最早已等待 {oldest_pending_seconds} 秒。" if oldest_pending_seconds is not None else "等待任务调度。"
        message = f"{pending_count} 名已结束学生的数据正在后台整理，完成后可刷新查看。{wait_note}"
    elif updating_count:
        state = "updating"
        message = f"{updating_count} 名学生已有数据可查看，数据有更新，正在等待后台整理新版本。"
    else:
        state = "ready"
        message = "研究数据已整理完成。" if totals["total"] else "当前范围暂无已完成学生数据。"
    current_version = current.aggregate(
        version=MD5(StringAgg(Cast('materialization_id', TextField()), delimiter=',', order_by='session_id')),
        updated_at=Max('finished_at'),
    )
    return {
        "state": state, "message": message,
        "retry_after_seconds": runtime_pressure()["retry_after_seconds"],
        "updated_at": current_version['updated_at'],
        "result_version": current_version['version'],
        "projection_version": PROJECTION_VERSION,
        "stale": bool(totals["stale_count"] or updating_count),
        "ready_count": totals["ready_count"], "pending_count": pending_count,
        "failed_count": failed_count, "updating_count": updating_count,
        "oldest_pending_seconds": oldest_pending_seconds,
    }


def materialized_advanced_status(scope, params):
    read_scope = replace(scope, session_id=None, session_ref=None) if params.get('view') == 'student' else scope
    sessions = _session_queryset(read_scope) if scope.is_complete else ExperimentSession.objects.none()
    return {'preparation': _preparation(sessions, _current_materializations(sessions))}


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
    terminal_session_queryset = (
        _session_queryset(read_scope)
        if scope.is_complete
        else ExperimentSession.objects.none()
    )
    current = _current_materializations(terminal_session_queryset)
    session_queryset = terminal_session_queryset.filter(pk__in=current.values("session_id"))
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

    selected_session, visible_sessions = _selected_session(
        session_queryset, scope, limit=50 if view == "student" else 20 if scope.student_id else 1
    )
    visible_ids = [item.pk for item in visible_sessions]
    summaries = dict(current.filter(session_id__in=visible_ids).values_list("session_id", "session_summary"))
    selected_current = current.filter(session_id=selected_session.pk).only("session_id", "session_summary").first() if selected_session else None
    session_summaries = [summaries[item.pk] for item in visible_sessions]
    student = None
    if scope.student_id and visible_sessions:
        selected_student = User.objects.filter(pk=scope.student_id).only(
            "student_number", "display_name", "first_name", "last_name", "username"
        ).first()
        if selected_student:
            student = _student_summary(course, selected_student, visible_sessions)

    responses = []
    visits = []
    question = None
    selected_visit = None
    if selected_current and view in {"session", "question", "timeline"}:
        response_rows = list(selected_current.response_rows.order_by("ordinal").only("payload")[:100])
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
            visit_rows = selected_current.visit_rows.order_by("ordinal").only("payload")
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
            summaries[selected_session.pk]
            if selected_session
            else None
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
    generated_at = current.aggregate(value=Max("finished_at"))["value"] or timezone.now()
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
        "preparation": _preparation(terminal_session_queryset, current),
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
    sessions = sessions.filter(
        research_materializations__status=ResearchSessionMaterialization.Status.SUCCEEDED,
        research_materializations__is_current=True,
        research_materializations__projection_version=PROJECTION_VERSION,
    )
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
    rows = students.only("student_number", "display_name", "first_name", "last_name", "username").order_by(
        "display_name", "student_number", "pk"
    ).distinct()[:20]
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
