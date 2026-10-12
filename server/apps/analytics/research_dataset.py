"""Course libraries and scoped research facts shared by preview and frozen export."""

import json
from collections import Counter, defaultdict
from dataclasses import replace
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.db.models import Max
from django.utils import timezone

from .models import ExportSnapshot
from .research_contract import FILES, event_dictionary
from .research_visits import build_visits, research_time
from .research_events import format_answer_value


TABLES = {
    "sessions": ("会话", "一名学生参加实验的一次会话"),
    "questions": ("题目", "课程题目固定库；详情中查看相关题目"),
    "knowledge_points": ("知识点", "课程知识点库；详情中查看相关知识点"),
    "question_kc_mapping": ("题目知识点映射", "题目与知识点的明确对应关系"),
    "responses": ("逐题作答", "本次实验实际分配的题目与最终作答事实"),
    "behavior_events": ("行为过程", "一次已记录的题内行为或状态变化"),
}


def table_catalog(dataset=None):
    return [
        {"key": key, "label": label, "path": f"{label}.csv", "description": description,
         "total_items": len(dataset[key][1]) if dataset else 0}
        for key, (label, description) in TABLES.items()
    ]


def scope_label(scope):
    parts = []
    experiment = scope.course.experiments.filter(pk=scope.experiment_id).first() if scope.experiment_id else None
    if experiment:
        parts.append(experiment.name)
    elif scope.experiment_ids:
        parts.append("、".join(scope.course.experiments.filter(pk__in=scope.experiment_ids).order_by("pk").values_list("name", flat=True)))
    if scope.class_snapshot_id:
        parts.append(scope._course_class_snapshot(scope.class_snapshot_id).display_name or "未记录班级")
    elif scope.class_id:
        parts.append(scope._course_class_group(scope.class_id).name)
    if scope.version_id:
        parts.append("限定历史发布范围")
    if scope.student_id:
        from apps.accounts.models import User

        student = User.objects.get(pk=scope.student_id)
        parts.append(f"{student.student_number or ''} {student.shown_name}".strip())
    if scope.search:
        parts.append(f"学生筛选：{scope.search}")
    if scope.session_ref:
        parts.append(scope.session_ref)
    elif scope.session_id:
        parts.append(scope.session_references().get(str(scope.session_id), "所选会话"))
    if scope.condition_id:
        parts.append(scope._course_condition(scope.condition_id).name)
    return " · ".join(parts) or "请选择实验和班级"


def records(tables, key):
    headers, rows = tables.get(key, ((), ()))
    return [dict(zip(headers, row)) for row in rows]


def parsed(value, default=None):
    if not isinstance(value, str):
        return default if value is None else value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def scalar(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def response_status(attempt, *, submitted, visited, skipped):
    if submitted:
        return "已提交"
    if attempt.get("status") == "timed_out":
        return "已超时"
    if attempt.get("status") == "skipped" or skipped:
        return "已跳过"
    if parsed(attempt.get("draft_answer")) not in (None, "", [], {}):
        return "已有答案未提交"
    if visited or attempt.get("status") == "visited":
        return "已访问未作答"
    return "未访问"


def build_research_bundle(captured, *, scope, include_course_catalog=True):
    from apps.accounts.models import CourseEnrollment
    from apps.assessment.models import ExperimentSession, QuestionVisitEvent
    from apps.question_bank.models import Concept, Question
    from .research_events import build_standard_behavior

    course = scope.course
    files = {item["key"]: [] for item in FILES}
    session_objects = {str(item.pk): item for item in captured["sessions"]}
    students = {}
    if include_course_catalog:
        from apps.question_bank.services.question_kc_mapping import (
            project_question_kc_mapping,
            question_kc_mapping_prefetch,
        )

        roster = list(CourseEnrollment.objects.filter(course=course).select_related("student", "class_group").order_by("student_id", "pk"))
        for enrollment in roster:
            student = enrollment.student
            students.setdefault(student.pk, {"id": student.pk, "student_number": student.student_number or "",
                "name": student.shown_name, "class_name": enrollment.class_group.name if enrollment.class_group else "",
                "course_name": course.name, "experiment_count": 0})
        files["course_students"] = [[course.name, item["student_number"], item["name"]] for item in students.values()]
        refs = scope.session_references()
        all_sessions = list(ExperimentSession.objects.filter(version__experiment__course=course)
            .select_related("student", "version__experiment").order_by("pk"))
        for session in all_sessions:
            if session.student_id not in students:
                students[session.student_id] = {"id": session.student_id, "student_number": session.student.student_number or "",
                    "name": session.student.shown_name, "class_name": session.class_snapshot_name,
                    "course_name": course.name, "experiment_count": 0}
            files["session_bindings"].append([course.name, session.student.student_number or "", session.student.shown_name,
                session.version.experiment_id, session.version.experiment.name, refs[str(session.pk)]])
        files["course_students"] = [[course.name, item["student_number"], item["name"]] for item in students.values()]
        files["course_knowledge"] = [[course.name, kc.code, kc.name] for kc in Concept.objects.filter(course=course).order_by("code", "pk")]
        questions = Question.objects.filter(bank__course=course).select_related(
            "course_identity"
        ).prefetch_related("options", question_kc_mapping_prefetch()).order_by(
            "course_identity__course_sequence", "pk"
        )
        for question in questions:
            qref = f"Q{question.course_identity.course_sequence:03d}"
            nodes = project_question_kc_mapping(question)["nodes"]
            files["course_mapping"].append([course.name, qref,
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 1),
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 2),
                ";".join(f"{node['parent_kc_code']}>{node['kc_code']}" for node in nodes if node["depth"] == 3)])
            files["question_library"].append({"题号": qref, "题干": question.text,
                "选项": {option.key: option.text for option in question.options.all()}, "答案": question.correct_answer,
                "解析": question.explanation, "难度": float(question.teacher_difficulty) if question.teacher_difficulty is not None else None})
    else:
        refs = {
            sid: f"S{session.started_at.astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}_{session.pk:04d}"
            for sid, session in session_objects.items()
        }
        for session in session_objects.values():
            students[session.student_id] = {"id": session.student_id,
                "student_number": session.student.student_number or "",
                "name": session.student.shown_name, "class_name": session.class_snapshot_name,
                "course_name": course.name, "experiment_count": 0}
    raw = captured["rows"]
    question_refs = {}
    question_types = {}
    for row in records(raw, "question_content"):
        snapshot_id = str(row["question_snapshot_id"])
        sequence = row.get("course_sequence") or row.get("source_sequence")
        question_refs[snapshot_id] = (
            f"Q{int(sequence):03d}" if sequence else row.get("question_code", "")
        )
        question_types[snapshot_id] = dict(Question.Type.choices).get(row.get("question_type"), "题型未记录")
    # The authoritative snapshot retains its course identity even after publication.
    for attempt in captured["attempts"]:
        snapshot = attempt.assignment.snapshot
        sequence = snapshot.course_sequence or snapshot.source_sequence
        question_refs[str(snapshot.pk)] = (
            f"Q{sequence:03d}"
            if sequence
            else snapshot.code
        )
    visits = build_visits(QuestionVisitEvent.objects.filter(session_id__in=session_objects)
        .select_related("attempt__assignment").order_by("session_id", "event_sequence", "pk"), captured["events"], question_refs)
    visits_by_session_question = defaultdict(list)
    first_visit_order_by_session_question = {}
    visit_question_counts = Counter()
    for visit in visits:
        sid = visit["session_id"]
        qref = visit["question_ref"]
        key = (sid, qref)
        visits_by_session_question[key].append(visit)
        if key not in first_visit_order_by_session_question:
            visit_question_counts[sid] += 1
            first_visit_order_by_session_question[key] = visit_question_counts[sid]
    identities = {sid: (item.student.student_number or "", item.student.shown_name) for sid, item in session_objects.items()}
    behavior = build_standard_behavior(captured, question_refs=question_refs, session_refs=refs, identities=identities, visits=visits)
    behavior_counts = Counter()
    for item in behavior:
        files["behavior_events"].append(item["row"])
        behavior_counts[item["session_id"]] += 1
    attempts = {(str(row["session_id"]), str(row["question_snapshot_id"])): row for row in records(raw, "attempts")}
    instances = {str(row["item_instance_id"]): row for row in records(raw, "item_instances")}
    submissions = {str(item.attempt_id): item for item in captured["submissions"]}
    responses = []
    skipped_attempts = {str(event.attempt_id) for event in captured["events"]
        if event.event_type == "item_left" and event.payload.get("leave_reason") == "skip"}
    response_counts = Counter()
    for assignment in sorted(records(raw, "assignments"), key=lambda row: (int(row["session_id"]), int(row["assigned_sequence_index"]))):
        sid, qid = str(assignment["session_id"]), str(assignment["question_snapshot_id"])
        attempt = attempts.get((sid, qid), {})
        qref = question_refs[qid]
        question_visits = visits_by_session_question[(sid, qref)]
        submission = submissions.get(str(attempt.get("attempt_id")))
        submitted = submission is not None and submission.committed_at is not None
        final_answer = format_answer_value(submission.final_answer) if submitted else ""
        correct = None
        if submitted and attempt.get("submission_grade_status") == "completed":
            correct = attempt.get("grade_is_correct")
            if correct in (None, ""):
                correct = attempt.get("is_correct")
            if correct not in (True, False):
                correct = {"True": True, "False": False, "true": True, "false": False}.get(str(correct))
        item = {"session_id": sid, "question_ref": qref, "question_type": question_types.get(qid, ""),
            "assigned_order": int(assignment["assigned_sequence_index"]),
            "first_visit_order": first_visit_order_by_session_question.get((sid, qref)),
            "visit_count": len(question_visits) if len(question_visits) == int(attempt.get("visit_count") or 0) else None,
            "clicked_times": [research_time(visit["clicked_at"]) for visit in question_visits],
            "entered_times": [research_time(visit["entered_at"]) for visit in question_visits],
            "left_times": [research_time(visit["left_at"]) for visit in question_visits],
            "submitted_at": research_time(submission.committed_at) if submitted else "",
            "final_answer": scalar(final_answer), "status_label": response_status(attempt, submitted=submitted,
                visited=bool(question_visits), skipped=str(attempt.get("attempt_id")) in skipped_attempts),
            "is_correct": correct}
        responses.append(item)
        response_counts[sid] += 1
        option_order = parsed(instances.get(str(assignment.get("item_instance_id")), {}).get("option_order"), [])
        files["responses"].append([refs[sid], *identities[sid], qref, item["assigned_order"], item["first_visit_order"],
            ";".join(map(str, option_order)), item["visit_count"], *[";".join(item[key]) for key in ("clicked_times", "entered_times", "left_times")],
            item["submitted_at"], item["final_answer"], item["status_label"], scalar(correct)])
    sessions = []
    for sid, session in session_objects.items():
        end = session.authoritative_terminal_at or session.completed_at
        sessions.append({"session_ref": refs[sid], "student_id": session.student_id,
            "student_number": identities[sid][0], "name": identities[sid][1], "course_name": course.name,
            "experiment_id": session.version.experiment_id, "experiment_name": session.version.experiment.name, "class_name": session.class_snapshot_name,
            "status_label": session.get_status_display(), "started_at": research_time(session.started_at),
            "ended_at": research_time(end), "submitted_at": research_time(session.completed_at),
            "duration_minutes": round((end - session.started_at).total_seconds() / 60, 2) if end else None,
            "question_count": response_counts[sid],
            "behavior_count": behavior_counts[sid]})
    files["event_dictionary"] = event_dictionary()
    headers = {item["key"]: item["headers"] for item in FILES}
    tables = {"sessions": ("sessionID 学号 姓名 课程 实验名称 开始时间 结束时间 时长(分钟)".split(),
        [[item[key] for key in ("session_ref", "student_number", "name", "course_name", "experiment_name", "started_at", "ended_at", "duration_minutes")] for item in sessions]),
        "questions": (list("题号 题干 选项 答案 解析 难度".split()), [[scalar(value) for value in item.values()] for item in files["question_library"]]),
        "knowledge_points": (headers["course_knowledge"], files["course_knowledge"]),
        "question_kc_mapping": (headers["course_mapping"], files["course_mapping"]),
        "responses": (headers["responses"], files["responses"]), "behavior_events": (headers["behavior_events"], files["behavior_events"])}
    return {"files": files, "tables": {key: (header, [[scalar(value) for value in row] for row in rows]) for key, (header, rows) in tables.items()},
        "students": list(students.values()), "sessions": sessions, "responses": responses, "visits": visits, "behavior": behavior}


def build_research_dataset(captured, *, scope):
    return build_research_bundle(captured, scope=scope)["tables"]


def read_research_bundle(scope, *, include_course_catalog=True):
    from django.db import connection, transaction
    from apps.assessment.models import AssessmentEvent
    from apps.experiments.models import ExperimentVersion
    from .services import _capture_export_source, _set_export_freeze_isolation

    outer = connection.in_atomic_block
    with transaction.atomic():
        if not outer:
            _set_export_freeze_isolation()
        now = timezone.now()
        versions = ExperimentVersion.objects.filter(experiment__course=scope.course)
        if scope.experiment_ids:
            versions = versions.filter(experiment_id__in=scope.experiment_ids)
        elif scope.experiment_id:
            versions = versions.filter(experiment_id=scope.experiment_id)
        if scope.version_id:
            versions = versions.filter(pk=scope.version_id)
        snapshot = ExportSnapshot(course=scope.course, source_cutoff_at=now, cutoff_at=now,
            filter_definition=scope.serialize(), event_high_watermark=AssessmentEvent.objects.filter(session__in=scope.sessions())
                .aggregate(value=Max("server_ingest_sequence_no"))["value"] or 0,
            selected_versions=[{"id": row.pk, "version": row.version_number} for row in versions])
        captured = _capture_export_source(
            SimpleNamespace(
                snapshot=snapshot,
                course_id=scope.course.pk,
                experiment=None,
                experiment_id=None,
                research_study=None,
                research_study_id=None,
            ),
            session_projection=not include_course_catalog,
        )
        return build_research_bundle(
            captured,
            scope=scope,
            include_course_catalog=include_course_catalog,
        ), now


def read_research_dataset(scope):
    bundle, now = read_research_bundle(scope)
    return bundle["tables"], now


def session_options(scope):
    base = replace(scope, search=None, session_ref=None, session_id=None)
    references = base.session_references()
    return [{"value": references[str(session.pk)], "label": f"{references[str(session.pk)]} · {session.student.student_number or ''} {session.student.shown_name}"}
            for session in base.sessions().select_related("student").order_by("pk")]


def workspace_payload(bundle, scope, query):
    empty = {"students": [], "student": None, "sessions": [], "session": None, "responses": [], "visits": [], "behavior": [], "question": None, "selected_visit": None}
    if bundle is None:
        return empty
    selected = next((item for item in bundle["sessions"] if item["session_ref"] == scope.session_ref), None)
    if selected is None and scope.session_id:
        selected = next((item for item in bundle["sessions"] if item["session_ref"] == scope.session_references().get(str(scope.session_id))), None)
    if selected is None and query.get("view") == "student" and bundle["sessions"]:
        selected = max(bundle["sessions"], key=lambda item: item["started_at"])
    sid = str(scope.session_id) if scope.session_id else next((key for key, ref in scope.session_references().items() if selected and ref == selected["session_ref"]), None)
    student_id = scope.student_id or (selected["student_id"] if selected else None)
    student = next((dict(item) for item in bundle["students"] if item["id"] == student_id), None)
    if student:
        student["experiment_count"] = len({item["experiment_id"] for item in bundle["sessions"] if item["student_id"] == student_id})
    responses = [item for item in bundle["responses"] if item["session_id"] == sid]
    session_visits = [item for item in bundle["visits"] if item["session_id"] == sid]
    visit = next((item for item in session_visits if item["key"] == query.get("visit")), None)
    if query.get("view") == "timeline" and visit is None and session_visits:
        visit = session_visits[0]
    qref = visit["question_ref"] if visit and query.get("view") == "timeline" else query.get("question_ref")
    question = next((item for item in responses if item["question_ref"] == qref), None)
    if query.get("view") == "question" and not qref and responses:
        question = responses[0]
    if query.get("view") == "question" and question:
        session_visits = [item for item in session_visits if item["question_ref"] == question["question_ref"]]
        if visit not in session_visits:
            visit = None
    behavior = [item for item in bundle["behavior"] if item["session_id"] == sid
        and (not question or item["question_ref"] == question["question_ref"])
        and (not visit or item.get("visit_key") == visit["key"])]
    return {"students": bundle["students"], "student": student, "sessions": sorted(bundle["sessions"], key=lambda item: item["started_at"], reverse=True), "session": selected,
        "responses": responses, "visits": session_visits,
        "behavior": [{key: value for key, value in item.items() if key not in {"row", "session_id"}} for item in behavior],
        "question": question, "selected_visit": visit["key"] if visit else None}


def context_tables(bundle, workspace, view):
    tables = dict(bundle["tables"])
    if view == "overview":
        return tables
    responses = workspace["responses"]
    if view == "student":
        selected_ref = (workspace["session"] or {}).get("session_ref")
        for key in ("sessions", "responses", "behavior_events"):
            headers, rows = tables[key]
            tables[key] = (headers, [row for row in rows if row[0] == selected_ref])
    qrefs = {item["question_ref"] for item in responses}
    if view == "question" and workspace["question"]:
        qrefs = {workspace["question"]["question_ref"]}
    headers, rows = tables["questions"]
    tables["questions"] = (headers, [row for row in rows if row[0] in qrefs])
    headers, rows = tables["question_kc_mapping"]
    mapping = [row for row in rows if row[1] in qrefs]
    tables["question_kc_mapping"] = (headers, mapping)
    krefs = set()
    for row in mapping:
        for cell in row[2:]:
            for relation in cell.split(";"):
                krefs.update(relation.split(">"))
    headers, rows = tables["knowledge_points"]
    tables["knowledge_points"] = (headers, [row for row in rows if row[1] in krefs])
    if view == "question":
        headers, rows = tables["responses"]
        tables["responses"] = (headers, [row for row in rows if row[3] in qrefs])
    if view in {"question", "timeline"}:
        header = tables["behavior_events"][0]
        session = workspace["session"]
        rows = [[session["session_ref"], session["student_number"], session["name"],
            *[scalar(item[key]) for key in ("question_ref", "visit_index", "sequence", "category", "action", "occurred_at", "before", "after", "details")]]
            for item in workspace["behavior"]] if session else []
        tables["behavior_events"] = (header, rows)
    return tables
