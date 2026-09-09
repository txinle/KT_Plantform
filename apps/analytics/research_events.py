"""Readable, de-identified projection of frozen assessment process evidence."""

from __future__ import annotations

import json
from collections import defaultdict


BEHAVIOR_HEADERS = (
    "学号",
    "姓名",
    "会话编号",
    "步骤序号",
    "题目编号",
    "行为类别",
    "具体行为",
    "发生时间",
    "时间依据",
    "时间可靠性",
    "服务器记录时间",
    "操作选项",
    "变化前答案",
    "变化后答案",
    "来源题目",
    "目标题目",
    "输入内容",
    "删除内容",
    "行为内容",
    "答案记录状态",
    "记录问题",
)


_EVENT_TABLES = (
    "assessment_events",
    "response_events",
    "navigation_events",
    "exposure_events",
    "telemetry_events",
)

_INCLUDED_EVENTS = {
    "response_transition",
    "input_operation",
    "response_input_started",
    "text_edit_observed",
    "response_state_changed",
    "response_committed",
    "item_entered",
    "item_rendered",
    "item_left",
    "legacy_visit_observed",
    "legacy_draft_observed",
    "legacy_submission_observed",
    "confidence_prompt_shown",
    "confidence_committed",
    "confidence_skipped",
    "confidence_observed",
    "feedback_shown",
    "feedback_exposure",
    "explanation_shown",
    "hint_opened",
    "hint_closed",
    "resource_opened",
    "resource_closed",
    "research_tool",
    "research_tool_requested",
    "research_tool_observed",
    "document_visible",
    "document_hidden",
    "window_focused",
    "window_blurred",
    "pageshow",
    "pagehide",
}

_QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "true_false": "判断题",
    "fill_blank": "填空题",
    "short_answer": "简答题",
    "numeric": "数值题",
}
_ENTRY_REASON_LABELS = {
    "direct_open": "直接打开题目",
    "session_start": "会话开始",
    "navigation_click": "点击导航",
    "skip_next": "跳过后进入下一题",
    "submit_next": "提交后进入下一题",
    "resume": "恢复作答",
}
_LEAVE_REASON_LABELS = {
    "navigation": "导航离开",
    "skip": "跳过题目",
    "submit": "提交后离开",
    "finish": "完成作答",
    "pagehide": "页面关闭或隐藏",
    "timeout": "作答超时",
}
_GRADING_STATUS_LABELS = {
    "pending": "待评分",
    "completed": "已评分",
    "regrade": "重新评分",
}
_CONFIDENCE_STAGE_LABELS = {
    "presented": "已呈现",
    "responded": "已作答",
    "skipped": "已跳过",
}
_FEEDBACK_PHASE_LABELS = {
    "before_feedback": "反馈前",
    "after_feedback": "反馈后",
    "unknown": "无法判断",
}
_CONFIDENCE_SKIP_REASON_LABELS = {
    "student_skipped": "学生跳过",
    "navigation": "导航离开",
    "session_closed": "会话已关闭",
    "upload_failed": "上传失败",
}
_FEEDBACK_MODE_LABELS = {
    "none": "无反馈",
    "correctness": "仅正误反馈",
    "full": "完整反馈",
}
_FEEDBACK_STAGE_LABELS = {
    "detail_entered": "进入详情",
    "answer_presented": "呈现答案",
    "explanation_presented": "呈现解析",
    "detail_left": "离开详情",
}
_FEEDBACK_SOURCE_LABELS = {
    "submission_feedback": "提交反馈",
    "review": "复习",
}
_RESEARCH_TOOL_LABELS = {
    "doubt": "标记疑问",
    "check": "检查答案",
    "hint": "查看提示",
    "confidence": "信心判断",
}
_RESEARCH_TOOL_STAGE_LABELS = {
    "available": "可用",
    "triggered": "已触发",
    "requested": "已请求",
    "presented": "已呈现",
    "closed": "已关闭",
    "mark_added": "添加标记",
    "mark_changed": "修改标记",
    "mark_removed": "移除标记",
    "started": "已开始",
    "finished": "已完成",
    "responded": "已作答",
}
_INITIATION_TYPE_LABELS = {
    "self_initiated": "学生主动",
    "system_prompted": "系统提示",
    "task_required": "任务要求",
}
_EDIT_OPERATION_LABELS = {
    "insert": "插入",
    "delete": "删除",
    "replace": "替换",
    "paste": "粘贴",
    "unknown": "未知编辑操作",
}
_INPUT_METHOD_LABELS = {
    "keyboard": "键盘",
    "ime": "输入法",
    "paste": "粘贴",
    "unknown": "未知输入方式",
}
_INPUT_TYPE_LABELS = {
    "change": "选择变化",
    "insertText": "输入文本",
    "insertReplacementText": "替换文本",
    "insertLineBreak": "插入换行",
    "insertParagraph": "插入段落",
    "insertFromPaste": "粘贴文本",
    "insertFromDrop": "拖放文本",
    "insertCompositionText": "输入法组合文本",
    "deleteByCut": "剪切删除",
    "deleteContent": "删除内容",
    "deleteContentBackward": "向前删除",
    "deleteContentForward": "向后删除",
    "deleteWordBackward": "向前删除词语",
    "deleteWordForward": "向后删除词语",
    "historyUndo": "撤销",
    "historyRedo": "重做",
}


def _json_value(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _table_dicts(tables, key):
    table = tables.get(key)
    if not table:
        return []
    headers, rows = table
    return [dict(zip(headers, row)) for row in rows]


def _present(value):
    return value is not None and value != ""


def _identifier(value):
    return str(value) if _present(value) else ""


def _number(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _enum_label(mapping, value, unknown_label):
    if not _present(value):
        return None
    return mapping.get(str(value), unknown_label)


def format_answer_value(value):
    """Return only the human-readable answer value from a frozen state."""
    if isinstance(value, dict):
        if "value" in value:
            return format_answer_value(value["value"])
        return None
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    return value


def _net_text_change(before, after):
    if not isinstance(before, str) or not isinstance(after, str):
        return None, None
    prefix = 0
    maximum_prefix = min(len(before), len(after))
    while prefix < maximum_prefix and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    maximum_suffix = min(len(before) - prefix, len(after) - prefix)
    while suffix < maximum_suffix and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    before_end = len(before) - suffix if suffix else len(before)
    after_end = len(after) - suffix if suffix else len(after)
    return after[prefix:after_end], before[prefix:before_end]


def _answer_from_legacy_state(state, snapshot_content_by_id):
    parsed = _json_value(state, state)
    if isinstance(parsed, dict):
        snapshot_id = _identifier(parsed.get("snapshot_id"))
        if snapshot_id:
            return snapshot_content_by_id.get(snapshot_id)
    return format_answer_value(parsed)


def _answer_from_legacy_payload(payload, which, snapshot_content_by_id):
    state_key = f"response_state_{which}"
    if state_key in payload:
        return _answer_from_legacy_state(payload[state_key], snapshot_content_by_id)
    if which != "after":
        return None
    snapshot_id = _identifier(payload.get("response_snapshot_id"))
    if snapshot_id in snapshot_content_by_id:
        return snapshot_content_by_id[snapshot_id]
    if _number(payload.get("content_length")) == 0:
        return ""
    return None


def _replay_evidence(tables):
    evidence = {}
    for row in _table_dicts(tables, "response_replay"):
        attempt_id = _identifier(row.get("attempt_id"))
        active_chain = _json_value(row.get("active_replay_chain"), []) or []
        final_chain = _json_value(row.get("final_replay_chain"), []) or []
        evidence[attempt_id] = {
            "active_ids": {
                _identifier(item.get("event_id"))
                for item in active_chain
                if isinstance(item, dict) and _present(item.get("event_id"))
            },
            "final_ids": {
                _identifier(item.get("event_id"))
                for item in final_chain
                if isinstance(item, dict) and _present(item.get("event_id"))
            },
            "active_status": row.get("active_replay_status") or "unknown",
            "final_status": row.get("final_replay_status") or "not_selected",
            "has_final": _present(row.get("final_response_event_id")),
        }
    return evidence


def _transition_record_status(event, replay_by_attempt):
    attempt_id = _identifier(event.get("attempt_id"))
    event_id = _identifier(event.get("event_id"))
    replay = replay_by_attempt.get(attempt_id)
    if replay is None:
        return "未评估", "缺少答案回放状态"

    active_ids = replay["active_ids"]
    final_ids = replay["final_ids"]
    adopted_ids = final_ids if replay["has_final"] else active_ids
    relevant_status = replay["final_status"] if replay["has_final"] else replay["active_status"]
    chain_problem = {
        "missing_transition": "答案变化链缺少上游记录",
        "cycle_detected": "答案变化链存在循环",
    }.get(relevant_status)
    if chain_problem:
        if event_id in adopted_ids:
            return "采用链（记录不完整）", chain_problem
        return "未采用分支（记录链不完整）", chain_problem
    if event_id in final_ids:
        return "正式提交链", None
    if event_id in active_ids:
        return "当前采用链", None
    return "未采用分支", None


def _time_fields(event):
    quality = event.get("time_quality") or event.get("interval_quality") or "unknown"
    derived = event.get("derived_occurred_at_utc") or event.get("estimated_occurred_at_utc")
    server_time = event.get("server_received_at") or event.get("server_committed_at") or None
    client_wall = event.get("client_wall_time_utc")
    if quality == "synchronized_estimate" and _present(derived):
        return derived, "同步估计时间", "时间为同步估计，可用于比较绝对时间", server_time
    if quality == "client_wall_unsynchronized" or (
        _present(client_wall) and quality not in {"synchronized_estimate", "server_only"}
    ):
        return (
            server_time,
            "服务器接收时间（客户端时钟未同步）",
            "客户端时间未同步，仅按服务器接收顺序",
            server_time,
        )
    if quality == "exact_monotonic":
        return (
            server_time,
            "服务器接收时间（客户端单调时钟仅用于排序）",
            "同一页面内步骤顺序可靠，绝对时间为服务器接收时间",
            server_time,
        )
    if _present(server_time):
        return server_time, "服务器接收时间", "仅有服务器记录时间", server_time
    if _present(client_wall):
        return client_wall, "客户端未校时时间", "时间仅供参考", None
    return None, "时间来源无法判断", "时间质量无法判断", None


def _event_rows(tables, attempt_context, *, include_all=False):
    events = []
    by_event_id = {}
    source_index = 0
    for table_key in _EVENT_TABLES:
        for row in _table_dicts(tables, table_key):
            event_type = row.get("event_type")
            if not include_all and event_type not in _INCLUDED_EVENTS:
                continue
            event_id = _identifier(row.get("event_id"))
            if event_id and event_id in by_event_id:
                existing = by_event_id[event_id]
                for key, value in row.items():
                    if not _present(existing.get(key)) and _present(value):
                        existing[key] = value
                continue
            item = dict(row)
            item["_source_index"] = source_index
            source_index += 1
            events.append(item)
            if event_id:
                by_event_id[event_id] = item

    seen_event_ids = set(by_event_id)
    for row in _table_dicts(tables, "response_input_observations"):
        source_event_id = _identifier(row.get("source_event_id"))
        if source_event_id and source_event_id in seen_event_ids:
            continue
        attempt_id = _identifier(row.get("attempt_id"))
        context = attempt_context.get(attempt_id, {})
        item = dict(row)
        item.update(
            {
                "event_id": source_event_id,
                "event_type": row.get("source_event_type") or "text_edit_observed",
                "session_id": context.get("session_id"),
                "question_snapshot_id": context.get("question_snapshot_id"),
                "payload": {
                    "edit_operation": row.get("edit_operation"),
                    "content_length_before": row.get("content_length_before"),
                    "content_length_after": row.get("content_length_after"),
                    "input_method": row.get("input_method"),
                    "is_ime": row.get("is_ime"),
                },
                "_source_index": source_index,
            }
        )
        source_index += 1
        events.append(item)
        if source_event_id:
            seen_event_ids.add(source_event_id)

    submitted_attempts = {
        _identifier(event.get("attempt_id"))
        for event in events
        if event.get("event_type") in {"response_committed", "legacy_submission_observed"}
    }
    for row in _table_dicts(tables, "submissions"):
        attempt_id = _identifier(row.get("attempt_id"))
        if attempt_id in submitted_attempts:
            continue
        context = attempt_context.get(attempt_id, {})
        events.append(
            {
                **row,
                "event_id": "",
                "event_type": "submission_recorded",
                "session_id": context.get("session_id"),
                "question_snapshot_id": context.get("question_snapshot_id"),
                "payload": {"grading_status": row.get("grading_status")},
                "_source_index": source_index,
            }
        )
        source_index += 1
        submitted_attempts.add(attempt_id)
    return events


def _ordered_session_events(events):
    def base_key(event):
        server_sequence = _number(event.get("server_ingest_sequence_no"))
        return (
            server_sequence is None,
            server_sequence if server_sequence is not None else 0,
            event.get("server_received_at") or event.get("committed_at") or "",
            event["_source_index"],
        )

    ordered = sorted(events, key=base_key)
    positions_by_instance = defaultdict(list)
    for index, event in enumerate(ordered):
        instance_id = _identifier(event.get("client_instance_id"))
        if instance_id:
            positions_by_instance[instance_id].append(index)
    for positions in positions_by_instance.values():
        members = sorted(
            (ordered[index] for index in positions),
            key=lambda event: (
                _number(event.get("client_sequence_no"), 10**30),
                base_key(event),
            ),
        )
        for index, event in zip(positions, members):
            ordered[index] = event

    event_ids = {_identifier(event.get("event_id")) for event in ordered}
    pending = list(ordered)
    result = []
    emitted = set()
    while pending:
        ready_index = next(
            (
                index
                for index, event in enumerate(pending)
                if not _identifier((_json_value(event.get("payload"), {}) or {}).get("parent_event_id"))
                or _identifier((_json_value(event.get("payload"), {}) or {}).get("parent_event_id")) not in event_ids
                or _identifier((_json_value(event.get("payload"), {}) or {}).get("parent_event_id")) in emitted
            ),
            None,
        )
        if ready_index is None:
            result.extend(pending)
            break
        event = pending.pop(ready_index)
        result.append(event)
        event_id = _identifier(event.get("event_id"))
        if event_id:
            emitted.add(event_id)
    return result


def _join_parts(*parts):
    return "；".join(str(part) for part in parts if _present(part)) or None


def _project_event(event, *, assignment_questions, question_refs, snapshot_content, replay_by_attempt):
    payload = _json_value(event.get("payload"), {}) or {}
    event_type = event.get("event_type")
    question_snapshot_id = _identifier(event.get("question_snapshot_id"))
    question = question_refs.get(question_snapshot_id) or event.get("question_code") or None
    projected = {
        "题目编号": question,
        "行为类别": None,
        "具体行为": None,
        "操作选项": None,
        "变化前答案": None,
        "变化后答案": None,
        "来源题目": None,
        "目标题目": None,
        "输入内容": None,
        "删除内容": None,
        "行为内容": None,
        "答案记录状态": None,
        "记录问题": None,
    }

    if event_type == "response_transition":
        before_state = payload.get("before_state")
        after_state = payload.get("after_state")
        before = format_answer_value(before_state)
        after = format_answer_value(after_state)
        question_type = None
        for response_state in (after_state, before_state):
            if isinstance(response_state, dict) and _present(response_state.get("question_type")):
                question_type = response_state["question_type"]
                break
        inserted, deleted = (
            _net_text_change(before, after)
            if question_type in {"short_answer", "fill_blank", "numeric"}
            else (None, None)
        )
        status, problem = _transition_record_status(event, replay_by_attempt)
        projected.update(
            {
                "行为类别": "作答",
                "具体行为": "答案状态变化",
                "操作选项": payload.get("option_key"),
                "变化前答案": before,
                "变化后答案": after,
                "输入内容": inserted,
                "删除内容": deleted,
                "行为内容": {
                    "select": "选择选项",
                    "deselect": "取消选择选项",
                    "clear": "清空答案",
                }.get(
                    payload.get("option_action"),
                    "由相邻答案状态计算的净变化"
                    if question_type in {"short_answer", "fill_blank", "numeric"}
                    and isinstance(before, str)
                    and isinstance(after, str)
                    else "记录完整答案状态变化",
                ),
                "答案记录状态": status,
                "记录问题": problem,
            }
        )
    elif event_type == "input_operation":
        composition = {
            "none": "非输入法组合",
            "started": "开始输入法组合",
            "updating": "输入法组合中",
            "confirmed": "输入法确认",
            "cancelled": "取消输入法组合",
        }.get(payload.get("composition"), "输入法状态未记录")
        selection = None
        if payload.get("selection_start") is not None or payload.get("selection_end") is not None:
            selection = f"选区：{payload.get('selection_start')}–{payload.get('selection_end')}"
        projected.update(
            {
                "行为类别": "输入过程",
                "具体行为": "输入操作",
                "行为内容": _join_parts(
                    f"输入类型：{_enum_label(_INPUT_TYPE_LABELS, payload.get('input_type'), '未识别输入类型')}"
                    if _present(payload.get("input_type"))
                    else None,
                    composition,
                    selection,
                ),
                "答案记录状态": "独立输入过程观测",
            }
        )
    elif event_type == "response_input_started":
        projected.update(
            {
                "行为类别": "作答",
                "具体行为": "开始作答",
                "行为内容": _enum_label(
                    _QUESTION_TYPE_LABELS,
                    payload.get("question_type"),
                    "未识别题型",
                ),
            }
        )
    elif event_type == "response_state_changed":
        before = _answer_from_legacy_payload(payload, "before", snapshot_content)
        after = _answer_from_legacy_payload(payload, "after", snapshot_content)
        snapshot_id = _identifier(payload.get("response_snapshot_id"))
        missing_snapshot = bool(snapshot_id and snapshot_id not in snapshot_content)
        projected.update(
            {
                "行为类别": "作答",
                "具体行为": "答案状态变化（旧采集）",
                "变化前答案": before,
                "变化后答案": after,
                "行为内容": "旧采集协议记录的答案变化",
                "答案记录状态": "旧采集记录",
                "记录问题": "缺少答案快照，无法还原文本" if missing_snapshot else None,
            }
        )
    elif event_type == "text_edit_observed":
        before_length = _number(payload.get("content_length_before"))
        after_length = _number(payload.get("content_length_after"))
        input_method = payload.get("input_method")
        is_ime = payload.get("is_ime") is True or input_method == "ime"
        input_method_label = _enum_label(
            _INPUT_METHOD_LABELS,
            input_method,
            "未识别输入方式",
        )
        projected.update(
            {
                "行为类别": "输入过程",
                "具体行为": "文本编辑观测",
                "行为内容": _join_parts(
                    f"编辑类型：{_enum_label(_EDIT_OPERATION_LABELS, payload.get('edit_operation'), '未识别编辑类型')}"
                    if _present(payload.get("edit_operation"))
                    else None,
                    f"长度：{before_length}→{after_length}" if before_length is not None and after_length is not None else None,
                    f"输入方式：{input_method_label}" if input_method_label else ("输入方式：输入法" if is_ime else None),
                ),
                "答案记录状态": "仅记录编辑元数据",
                "记录问题": "未采集具体输入和删除文本",
            }
        )
    elif event_type in {"item_entered", "item_rendered", "item_left", "legacy_visit_observed"}:
        names = {
            "item_entered": "进入题目",
            "item_rendered": "呈现题目",
            "item_left": "离开题目",
            "legacy_visit_observed": "进入题目",
        }
        from_snapshot = assignment_questions.get(_identifier(payload.get("from_assignment_id")))
        to_snapshot = assignment_questions.get(_identifier(payload.get("to_assignment_id")))
        projected.update(
            {
                "行为类别": "访问与导航",
                "具体行为": names[event_type],
                "来源题目": question_refs.get(from_snapshot) if from_snapshot else (question if event_type == "item_left" else None),
                "目标题目": question_refs.get(to_snapshot) if to_snapshot else (question if event_type != "item_left" else None),
                "行为内容": _join_parts(
                    _enum_label(
                        _ENTRY_REASON_LABELS,
                        payload.get("entry_reason"),
                        "未识别进入原因",
                    ),
                    _enum_label(
                        _LEAVE_REASON_LABELS,
                        payload.get("leave_reason"),
                        "未识别离开原因",
                    ),
                    f"第{payload.get('visit_index')}次访问" if _present(payload.get("visit_index")) else None,
                ),
                "记录问题": "历史记录未采集完整页面过程" if event_type == "legacy_visit_observed" else None,
            }
        )
    elif event_type in {"response_committed", "legacy_submission_observed", "submission_recorded"}:
        final_snapshot_id = _identifier(
            event.get("final_response_snapshot_id")
            or payload.get("final_response_snapshot_id")
        )
        final_answer = snapshot_content.get(final_snapshot_id) if final_snapshot_id else None
        projected.update(
            {
                "行为类别": "提交",
                "具体行为": "提交答案",
                "变化后答案": final_answer,
                "行为内容": _join_parts(
                    f"评分状态：{_enum_label(_GRADING_STATUS_LABELS, payload.get('grading_status'), '未识别评分状态')}"
                    if _present(payload.get("grading_status"))
                    else None,
                    "历史提交记录" if event_type == "legacy_submission_observed" else None,
                ),
                "答案记录状态": "已正式提交",
                "记录问题": (
                    "缺少最终答案快照，无法还原答案"
                    if final_snapshot_id and final_snapshot_id not in snapshot_content
                    else None
                ),
            }
        )
    elif event_type == "legacy_draft_observed":
        projected.update(
            {
                "行为类别": "作答",
                "具体行为": "保存草稿（旧采集）",
                "答案记录状态": "仅确认保存事实",
                "记录问题": "历史记录未保留答案内容",
            }
        )
    elif event_type.startswith("confidence_"):
        name = {
            "confidence_prompt_shown": "呈现信心问题",
            "confidence_committed": "提交信心判断",
            "confidence_skipped": "跳过信心判断",
            "confidence_observed": "信心判断观测",
        }[event_type]
        probability = payload.get("probability")
        stage_label = _enum_label(
            _CONFIDENCE_STAGE_LABELS,
            payload.get("stage"),
            "未识别信心阶段",
        )
        feedback_phase_label = _enum_label(
            _FEEDBACK_PHASE_LABELS,
            payload.get("feedback_phase"),
            "未识别反馈阶段",
        )
        skip_reason_label = _enum_label(
            _CONFIDENCE_SKIP_REASON_LABELS,
            payload.get("reason_code"),
            "未识别跳过原因",
        )
        projected.update(
            {
                "行为类别": "信心判断",
                "具体行为": name,
                "操作选项": probability,
                "行为内容": _join_parts(
                    f"信心值：{probability}%" if probability is not None else None,
                    f"阶段：{stage_label}" if stage_label else None,
                    f"反馈阶段：{feedback_phase_label}" if feedback_phase_label else None,
                    f"跳过原因：{skip_reason_label}" if skip_reason_label else None,
                ),
            }
        )
    elif event_type in {"feedback_shown", "feedback_exposure", "explanation_shown"}:
        included = []
        for key, label in (
            ("included_correctness", "正误"),
            ("included_correct_answer", "正确答案"),
            ("included_explanation", "解析"),
        ):
            if payload.get(key) is True:
                included.append(label)
        projected.update(
            {
                "行为类别": "反馈",
                "具体行为": "呈现解析" if event_type == "explanation_shown" else "呈现反馈",
                "行为内容": _join_parts(
                    f"反馈模式：{_enum_label(_FEEDBACK_MODE_LABELS, payload.get('feedback_mode'), '未识别反馈模式')}"
                    if _present(payload.get("feedback_mode"))
                    else None,
                    f"阶段：{_enum_label(_FEEDBACK_STAGE_LABELS, payload.get('stage'), '未识别反馈阶段')}"
                    if _present(payload.get("stage"))
                    else None,
                    f"包含：{'、'.join(included)}" if included else None,
                    f"来源：{_enum_label(_FEEDBACK_SOURCE_LABELS, payload.get('source'), '未识别反馈来源')}"
                    if _present(payload.get("source"))
                    else None,
                ),
            }
        )
    elif event_type in {"hint_opened", "hint_closed", "resource_opened", "resource_closed"}:
        projected.update(
            {
                "行为类别": "学习支持",
                "具体行为": {
                    "hint_opened": "打开提示",
                    "hint_closed": "关闭提示",
                    "resource_opened": "打开资源",
                    "resource_closed": "关闭资源",
                }[event_type],
                "行为内容": payload.get("resource_type"),
            }
        )
    elif event_type in {"research_tool", "research_tool_requested", "research_tool_observed"}:
        tool_label = _enum_label(
            _RESEARCH_TOOL_LABELS,
            payload.get("tool"),
            "未识别研究工具",
        )
        stage_label = _enum_label(
            _RESEARCH_TOOL_STAGE_LABELS,
            payload.get("stage"),
            "未识别研究工具阶段",
        )
        initiation_label = _enum_label(
            _INITIATION_TYPE_LABELS,
            payload.get("initiation_type"),
            "未识别发起方式",
        )
        projected.update(
            {
                "行为类别": "研究工具",
                "具体行为": "使用研究工具",
                "操作选项": tool_label,
                "行为内容": _join_parts(
                    f"工具：{tool_label}" if tool_label else None,
                    f"阶段：{stage_label}" if stage_label else None,
                    f"发起方式：{initiation_label}" if initiation_label else None,
                ),
            }
        )
    else:
        page_names = {
            "document_visible": "页面可见",
            "document_hidden": "页面不可见",
            "window_focused": "窗口获得焦点",
            "window_blurred": "窗口失去焦点",
            "pageshow": "页面显示",
            "pagehide": "页面隐藏",
        }
        projected.update(
            {
                "行为类别": "页面状态",
                "具体行为": page_names[event_type],
                "行为内容": (
                    f"从页面缓存恢复：{'是' if payload.get('persisted') else '否'}"
                    if "persisted" in payload
                    else None
                ),
            }
        )
    return projected


def build_behavior_rows(tables, *, session_refs, question_refs, identity_by_session):
    """Build de-identified behavior rows from frozen raw export tables."""
    assignment_questions = {
        _identifier(row.get("assignment_id")): _identifier(row.get("question_snapshot_id"))
        for row in _table_dicts(tables, "assignments")
        if _present(row.get("assignment_id"))
    }
    attempt_context = {}
    for row in _table_dicts(tables, "attempts"):
        if not _present(row.get("attempt_id")):
            continue
        attempt_context[_identifier(row.get("attempt_id"))] = {
            "session_id": _identifier(row.get("session_id")),
            "question_snapshot_id": _identifier(row.get("question_snapshot_id")),
        }
    snapshot_content = {
        _identifier(row.get("snapshot_id")): row.get("content")
        for row in _table_dicts(tables, "response_snapshots")
        if _present(row.get("snapshot_id"))
    }
    replay_by_attempt = _replay_evidence(tables)
    events = _event_rows(tables, attempt_context)
    events_by_session = defaultdict(list)
    for event in events:
        attempt = attempt_context.get(_identifier(event.get("attempt_id")), {})
        session_id = _identifier(event.get("session_id")) or attempt.get("session_id", "")
        question_snapshot_id = _identifier(event.get("question_snapshot_id")) or attempt.get("question_snapshot_id", "")
        event["session_id"] = session_id
        event["question_snapshot_id"] = question_snapshot_id
        events_by_session[session_id].append(event)

    result = []
    ordered_sessions = sorted(
        events_by_session,
        key=lambda session_id: (str(session_refs.get(session_id, "")), session_id),
    )
    for session_id in ordered_sessions:
        identity = identity_by_session.get(session_id, ("", ""))
        session_ref = session_refs.get(session_id, "")
        for step, event in enumerate(_ordered_session_events(events_by_session[session_id]), 1):
            projection = _project_event(
                event,
                assignment_questions=assignment_questions,
                question_refs=question_refs,
                snapshot_content=snapshot_content,
                replay_by_attempt=replay_by_attempt,
            )
            occurred_at, time_basis, time_reliability, server_time = _time_fields(event)
            values = {
                "学号": identity[0],
                "姓名": identity[1],
                "会话编号": session_ref,
                "步骤序号": step,
                "发生时间": occurred_at,
                "时间依据": time_basis,
                "时间可靠性": time_reliability,
                "服务器记录时间": server_time,
                **projection,
            }
            result.append([values.get(header) for header in BEHAVIOR_HEADERS])
    return result


STANDARD_EVENTS = {
    'item_entered': ('题目访问', '进入题目'), 'item_rendered': ('题目访问', '题目呈现'),
    'item_left': ('题目访问', '离开题目'), 'legacy_visit_observed': ('题目访问', '历史访问记录'),
    'input_operation': ('输入操作', '操作请求'), 'response_transition': ('答案交互', '修改答案'),
    'response_state_changed': ('答案交互', '答案状态变化'), 'text_edit_observed': ('文本输入', '文本编辑记录'),
    'response_input_started': ('文本输入', '开始输入'), 'legacy_draft_observed': ('答案交互', '历史草稿记录'),
    'response_commit_requested': ('提交过程', '请求提交答案'), 'response_commit_rejected': ('提交过程', '答案提交被拒绝'),
    'response_committed': ('提交过程', '正式提交成功'), 'legacy_submission_observed': ('提交过程', '历史提交记录'),
    'submission_recorded': ('提交过程', '正式提交记录'),
    'attempt_closed': ('提交过程', '题目作答关闭'),
    'response_pointer_conflict_observed': ('技术状态', '答案指针冲突'), 'response_branch_resolved': ('技术状态', '答案分支解决'),
    'window_blurred': ('页面状态', '窗口失焦'), 'window_focused': ('页面状态', '窗口获得焦点'),
    'document_hidden': ('页面状态', '页面不可见'), 'document_visible': ('页面状态', '页面恢复可见'),
    'pageshow': ('技术状态', '页面显示'), 'pagehide': ('技术状态', '页面隐藏'),
    'network_offline': ('技术状态', '网络断开'), 'network_online': ('技术状态', '网络恢复'),
    'activity_heartbeat': ('技术状态', '活动心跳'),
    'confidence_prompt_shown': ('信心记录', '信心题展示'), 'confidence_observed': ('信心记录', '信心交互'),
    'confidence_committed': ('信心记录', '提交信心'), 'confidence_skipped': ('信心记录', '跳过信心'),
    'hint_opened': ('资源/提示', '提示打开'), 'hint_closed': ('资源/提示', '提示关闭'),
    'resource_opened': ('资源/提示', '资源打开'), 'resource_closed': ('资源/提示', '资源关闭'),
    'feedback_shown': ('反馈/解析', '反馈呈现'), 'feedback_exposure': ('反馈/解析', '反馈交互'),
    'explanation_shown': ('反馈/解析', '解析呈现'),
    'research_tool': ('研究工具', '工具交互'), 'research_tool_requested': ('研究工具', '请求工具'),
    'research_tool_observed': ('研究工具', '工具状态记录'),
    'simulation_parameter_changed': ('仿真过程', '修改仿真参数'), 'simulation_run_started': ('仿真过程', '启动仿真'),
    'simulation_run_completed': ('仿真过程', '仿真完成'), 'simulation_error_returned': ('仿真过程', '仿真错误'),
}

TOOL_ACTIONS = {
    ('doubt', 'mark_added'): '添加疑问标记', ('doubt', 'mark_changed'): '修改疑问标记',
    ('doubt', 'mark_removed'): '取消疑问标记', ('check', 'started'): '开始检查答案',
    ('check', 'finished'): '完成检查答案', ('hint', 'available'): '提示可用',
    ('hint', 'presented'): '提示呈现', ('hint', 'closed'): '关闭提示',
}
FEEDBACK_ACTIONS = {'detail_entered': '查看反馈详情', 'answer_presented': '答案呈现',
    'explanation_presented': '解析呈现', 'detail_left': '离开反馈详情'}


def build_standard_behavior(captured, *, question_refs, session_refs, identities, visits):
    from .research_visits import match_visit, research_time
    tables = captured['rows']
    attempts = {str(row['attempt_id']): row for row in _table_dicts(tables, 'attempts')}
    assignments = {str(row['assignment_id']): str(row['question_snapshot_id']) for row in _table_dicts(tables, 'assignments')}
    contents = {str(row['snapshot_id']): row.get('content') for row in _table_dicts(tables, 'response_snapshots')}
    replay = _replay_evidence(tables)
    native = {str(event.event_id): event for event in captured['events']}
    grouped = defaultdict(list)
    for event in _event_rows(tables, attempts, include_all=True):
        attempt = attempts.get(str(event.get('attempt_id')), {})
        sid = str(event.get('session_id') or attempt.get('session_id') or '')
        qid = str(event.get('question_snapshot_id') or attempt.get('question_snapshot_id') or '')
        if sid not in identities or not question_refs.get(qid):
            continue
        event.update(session_id=sid, question_snapshot_id=qid)
        grouped[sid].append(event)
    result = []
    for sid, events in grouped.items():
        sequences = defaultdict(int)
        confidence_values = {}
        for event in _ordered_session_events(events):
            kind = event.get('event_type')
            payload = _json_value(event.get('payload'), {}) or {}
            qref = question_refs[event['question_snapshot_id']]
            category, action = STANDARD_EVENTS.get(kind, ('其他题内记录', str(kind)))
            projection = _project_event(event, assignment_questions=assignments, question_refs=question_refs,
                snapshot_content=contents, replay_by_attempt=replay) if kind in _INCLUDED_EVENTS else {}
            before, after = projection.get('变化前答案'), projection.get('变化后答案')
            if kind == 'response_transition':
                state = payload.get('after_state') or payload.get('before_state') or {}
                question_type = state.get('question_type') if isinstance(state, dict) else None
                if question_type in {'short_answer', 'fill_blank', 'numeric'}:
                    category = '文本输入'
                    input_type = payload.get('input_type')
                    action = {'insertFromPaste': '粘贴文本', 'deleteByCut': '剪切文本', 'historyUndo': '撤销', 'historyRedo': '重做'}.get(input_type)
                    if action is None:
                        action = '清空答案' if after in ('', []) else ('删除文本' if isinstance(before, str) and isinstance(after, str) and len(after) < len(before) else '输入文本')
                    if action == '清空答案':
                        category = '答案交互'
                else:
                    action = '清空答案' if after in ('', [], '[]') else {'select': '选择选项', 'deselect': '取消选择', 'clear': '清空答案'}.get(payload.get('option_action'), '选择选项' if before in (None, '', [], '[]') else '修改答案')
            elif kind == 'input_operation':
                action = _INPUT_TYPE_LABELS.get(payload.get('input_type'), '操作请求')
                before, after = None, None
            elif kind == 'confidence_observed':
                confidence_key = payload.get('confidence_assignment_id')
                if payload.get('stage') == 'responded':
                    before, after = confidence_values.get(confidence_key), payload.get('probability')
                    action = '选择信心' if confidence_key not in confidence_values else '修改信心'
                    confidence_values[confidence_key] = after
                elif payload.get('stage') == 'presented':
                    action = '信心题展示'
                elif payload.get('stage') == 'skipped':
                    action = '跳过信心'
            elif kind == 'confidence_committed':
                after = payload.get('probability')
            elif kind in {'research_tool', 'research_tool_requested', 'research_tool_observed'}:
                tool, stage = payload.get('tool'), payload.get('stage')
                action = TOOL_ACTIONS.get((tool, stage),
                    f"{_RESEARCH_TOOL_LABELS.get(tool, '工具')}：{_RESEARCH_TOOL_STAGE_LABELS.get(stage, '交互记录')}")
            elif kind == 'feedback_exposure':
                action = FEEDBACK_ACTIONS.get(payload.get('stage'), '反馈交互')
            elif kind == 'item_left' and payload.get('leave_reason') == 'skip':
                action = '跳过题目'
            elif kind in {'window_blurred', 'window_focused', 'document_hidden', 'document_visible', 'network_offline', 'network_online'}:
                before, after = {'window_blurred': ('focused', 'blurred'), 'window_focused': ('blurred', 'focused'),
                    'document_hidden': ('visible', 'hidden'), 'document_visible': ('hidden', 'visible'),
                    'network_offline': ('online', 'offline'), 'network_online': ('offline', 'online')}[kind]
            source = native.get(str(event.get('event_id')))
            visit = match_visit(source, visits) if source else None
            occurred = None
            basis = '发生时间未记录'
            if source:
                if source.actor_source == 'server':
                    occurred, basis = source.server_committed_at, '服务器确认时间'
                elif source.estimated_occurred_at_utc:
                    occurred, basis = source.estimated_occurred_at_utc, '同步估计时间'
                elif source.client_wall_time_utc:
                    occurred, basis = source.client_wall_time_utc, '客户端时间（未同步）'
            elif kind == 'submission_recorded':
                occurred, basis = event.get('committed_at'), '服务器提交记录时间'
            def public_content(value):
                if isinstance(value, dict):
                    return {key: public_content(item) for key, item in value.items()
                        if not key.endswith(('_id', '_ids', '_hash', '_version'))
                        and key not in {'capture_version', 'schema_version', 'snapshot_id', 'content_hash'}}
                if isinstance(value, list):
                    return [public_content(item) for item in value]
                return value
            details = {'事件类型': kind, '时间依据': basis, '操作内容': public_content(payload)}
            if source:
                details['服务器记录时间'] = research_time(source.server_committed_at)
            if not visit:
                details['访问归属'] = '无法确定'
            for key in ('行为内容', '记录问题', '答案记录状态', '来源题目', '目标题目'):
                if projection.get(key):
                    details[key] = projection[key]
            sequences[qref] += 1
            def cell(value):
                if value is None:
                    return ''
                return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            item = {'session_id': sid, 'question_ref': qref, 'visit_index': visit['visit_index'] if visit else None,
                'visit_key': visit['key'] if visit else None, 'sequence': sequences[qref], 'category': category, 'action': action,
                'occurred_at': research_time(occurred), 'before': cell(before), 'after': cell(after),
                'details': json.dumps(details, ensure_ascii=False)}
            item['row'] = [session_refs[sid], *identities[sid], qref, item['visit_index'], item['sequence'],
                category, action, item['occurred_at'], item['before'], item['after'], item['details']]
            result.append(item)
    return result
