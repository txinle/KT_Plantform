"""Data-quality reads over promoted per-session research projections."""

from apps.assessment.models import ExperimentSession

from .advanced_materialized import (
    _current_materializations,
    _preparation,
    _session_queryset,
)
from .research_materialization import PROJECTION_VERSION


STATE_LABELS = {
    "PASS": "记录正常",
    "WARN": "需要注意",
    "FAIL": "记录不一致",
    "NOT_EVALUABLE": "无法评估",
    "NOT_APPLICABLE": "不适用",
}

EXPLANATIONS = {
    "missing_native_event_anchor": ("缺少原生行为记录", "无法核验行为与题目的对应关系"),
    "identity_anchor_mismatch": ("行为记录与题目对应关系不一致", "不能可靠关联该行为"),
    "missing_native_state_observation": ("缺少原生答案状态记录", "不能完整还原答案变化"),
    "no_response_state_event": ("没有答案变化记录", "不适用答案状态链检查"),
    "response_state_chain_mismatch": ("答案变化记录不连续或不一致", "不能完整还原答案变化"),
    "attempt_not_submitted": ("尚未正式提交", "没有可供核验的正式最终答案"),
    "missing_submission": ("作答显示已提交但缺少正式提交记录", "无法确认最终答案"),
    "submission_snapshot_mismatch": ("正式提交与答案记录不一致", "最终答案需要核查"),
    "missing_client_sequence": ("缺少客户端顺序记录", "不能精确判断学生操作顺序和间隔"),
    "client_sequence_not_strict": ("客户端顺序记录存在冲突", "操作先后顺序需要核查"),
    "interval_degraded": ("时间记录精度不足", "不能按精确时长解释操作间隔"),
    "export_scoped_rule": ("本项在生成数据包时检查", "实时预览中不作判断"),
}


def materialized_quality_payload(scope):
    terminal_sessions = (
        _session_queryset(scope) if scope.is_complete else ExperimentSession.objects.none()
    )
    current = [
        item
        for item in _current_materializations(terminal_sessions)
        if item.projection_version == PROJECTION_VERSION
    ]
    items = []
    counts = {label: 0 for label in STATE_LABELS.values()}
    for materialization in current:
        summary = materialization.session_summary
        for row in materialization.quality_rows:
            state = row.get("state", "NOT_EVALUABLE")
            state_label = STATE_LABELS.get(state, state)
            problem, impact = EXPLANATIONS.get(
                row.get("reason_code", ""), ("本项记录核验正常", "未发现影响")
            )
            items.append(
                {
                    "student_number": summary.get("student_number", ""),
                    "student_name": summary.get("name", ""),
                    "session_ref": summary.get("session_ref", ""),
                    "question_code": row.get("question_code", ""),
                    "state": state,
                    "state_label": state_label,
                    "problem": problem,
                    "impact": impact,
                }
            )
            if state_label in counts:
                counts[state_label] += 1
    return {
        "items": items,
        "counts": counts,
        "preparation": _preparation(terminal_sessions, current),
    }
