from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class TeacherProcessMessage:
    title: str
    description: str


_COVERAGE_STATUS_MESSAGES = MappingProxyType(
    {
        "complete": TeacherProcessMessage("过程记录完整", "已记录本题所需的过程边界，可用于过程回顾。"),
        "partial": TeacherProcessMessage("过程记录不完整", "可确认最终结果，但部分作答步骤未被记录，不能完整还原过程。"),
        "insufficient": TeacherProcessMessage("过程记录不足", "记录不足，不能分析作答过程。"),
        "invalid": TeacherProcessMessage("记录存在不一致", "答案快照、提交或评分事实彼此不一致。"),
        "legacy_unknown": TeacherProcessMessage("历史记录无法评估", "旧记录没有当前采集契约，不能按新标准判断。"),
        "not_applicable": TeacherProcessMessage("不适用", "当前题目或任务不要求该类过程记录。"),
        "pending_finalization": TeacherProcessMessage("过程记录等待确认", "本次作答刚结束，系统仍在等待可能迟到的过程记录。"),
    }
)
_UNKNOWN_COVERAGE_STATUS = TeacherProcessMessage("记录状态无法判断", "系统未能识别本次记录的质量状态。")

_TIME_QUALITY_MESSAGES = MappingProxyType(
    {
        "exact_monotonic": TeacherProcessMessage("步骤间隔可可靠比较", "同一页面内记录的先后顺序和步骤间隔可用于分析。"),
        "synchronized_estimate": TeacherProcessMessage("时间为同步估计", "可比较绝对时间，但属于校时估计。"),
        "client_wall_unsynchronized": TeacherProcessMessage("时间仅供参考", "有客户端时间记录，但未完成有效校时，不作精确间隔判断。"),
        "server_only": TeacherProcessMessage("仅有服务器记录时间", "可确认服务器收到或提交的顺序，不能视为学生实际操作时刻。"),
        "unknown": TeacherProcessMessage("时间质量无法判断", "不对步骤间隔作解释。"),
    }
)

_COVERAGE_REASON_LABELS = MappingProxyType(
    {
        "missing_final_snapshot": "未保存最终答案快照",
        "missing_item_entered": "未记录进入题目",
        "missing_item_left": "未记录离开题目",
        "missing_item_rendered": "未记录题目呈现",
        "missing_response_state": "未记录答案状态变化",
        "response_chain_break": "答案变化记录不连续",
        "sequence_gap": "过程记录序号存在缺口",
        "tail_gap": "页面结束前仍有未确认记录",
        "tail_unknown": "页面结束前仍有未确认记录",
        "checkpoint_missing": "未收到记录检查点",
        "time_sync_unavailable": "未完成有效时间同步",
        "cross_instance_order_uncertain": "跨页面先后顺序无法精确确定",
        "coverage_run_missing": "尚未生成记录质量评估",
        "pending_finalization": "过程记录仍在等待确认",
        "final_snapshot_mismatch": "最终答案快照与正式提交不一致",
        "submission_chain_mismatch": "正式提交与作答关闭记录不一致",
        "legacy_contract": "历史记录没有当前采集契约",
        "dead_letter_event": "部分过程记录未能上传",
        "persistent_outbox_unavailable": "设备未能持续保存过程记录",
        "time_origin_changed": "页面时间基准发生变化",
        "telemetry_baseline_missing": "未记录页面初始状态",
        "collector_unavailable": "未收到客户端过程记录",
    }
)

_EVENT_TIME_SOURCE_LABELS = MappingProxyType(
    {
        "synchronized_estimate": "同步估计时间",
        "server_received_at": "服务器接收时间",
        "unknown": "时间来源无法判断",
    }
)

_CLOSE_REASON_LABELS = MappingProxyType(
    {
        "submitted": "已提交",
        "timed_out": "作答超时",
        "unanswered": "未作答关闭",
        "invalidated": "作答已作废",
    }
)


def coverage_status_message(status):
    return _COVERAGE_STATUS_MESSAGES.get(status, _UNKNOWN_COVERAGE_STATUS)


def time_quality_message(status):
    return _TIME_QUALITY_MESSAGES.get(status, _TIME_QUALITY_MESSAGES["unknown"])


def coverage_reason_labels(reasons):
    return tuple(_COVERAGE_REASON_LABELS.get(reason, "存在未分类的记录缺口") for reason in reasons)


def event_time_source_message(source):
    return _EVENT_TIME_SOURCE_LABELS.get(source, _EVENT_TIME_SOURCE_LABELS["unknown"])


def close_reason_label(reason):
    return _CLOSE_REASON_LABELS.get(reason, "关闭原因未记录")
