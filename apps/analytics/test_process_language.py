from dataclasses import FrozenInstanceError

from django.test import SimpleTestCase

from .teaching.process_language import (
    TeacherProcessMessage,
    coverage_reason_labels,
    coverage_status_message,
    event_time_source_message,
    time_quality_message,
)


class TeacherProcessLanguageTests(SimpleTestCase):
    def test_message_is_immutable(self):
        message = TeacherProcessMessage("标题", "说明")
        with self.assertRaises(FrozenInstanceError):
            message.title = "修改"

    def test_all_coverage_statuses_have_frozen_messages(self):
        expected = {
            "complete": ("过程记录完整", "已记录本题所需的过程边界，可用于过程回顾。"),
            "partial": ("过程记录不完整", "可确认最终结果，但部分作答步骤未被记录，不能完整还原过程。"),
            "insufficient": ("过程记录不足", "记录不足，不能分析作答过程。"),
            "invalid": ("记录存在不一致", "答案快照、提交或评分事实彼此不一致。"),
            "legacy_unknown": ("历史记录无法评估", "旧记录没有当前采集契约，不能按新标准判断。"),
            "not_applicable": ("不适用", "当前题目或任务不要求该类过程记录。"),
            "pending_finalization": ("过程记录等待确认", "本次作答刚结束，系统仍在等待可能迟到的过程记录。"),
        }
        for status, values in expected.items():
            with self.subTest(status=status):
                self.assertEqual(coverage_status_message(status), TeacherProcessMessage(*values))

    def test_unknown_coverage_status_has_fallback_message(self):
        self.assertEqual(
            coverage_status_message("future_status"),
            TeacherProcessMessage("记录状态无法判断", "系统未能识别本次记录的质量状态。"),
        )

    def test_all_time_quality_statuses_have_messages_and_unknown_falls_back(self):
        expected = {
            "exact_monotonic": ("步骤间隔可可靠比较", "同一页面内记录的先后顺序和步骤间隔可用于分析。"),
            "synchronized_estimate": ("时间为同步估计", "可比较绝对时间，但属于校时估计。"),
            "client_wall_unsynchronized": ("时间仅供参考", "有客户端时间记录，但未完成有效校时，不作精确间隔判断。"),
            "server_only": ("仅有服务器记录时间", "可确认服务器收到或提交的顺序，不能视为学生实际操作时刻。"),
            "unknown": ("时间质量无法判断", "不对步骤间隔作解释。"),
        }
        for status, values in expected.items():
            with self.subTest(status=status):
                self.assertEqual(time_quality_message(status), TeacherProcessMessage(*values))
        self.assertEqual(time_quality_message("future_quality"), TeacherProcessMessage(*expected["unknown"]))

    def test_partial_and_exact_monotonic_do_not_expose_raw_identifiers(self):
        self.assertNotIn("partial", repr(coverage_status_message("partial")))
        self.assertNotIn("exact_monotonic", repr(time_quality_message("exact_monotonic")))

    def test_known_and_unknown_reason_labels(self):
        self.assertEqual(
            coverage_reason_labels(["missing_final_snapshot", "unknown_reason"]),
            ("未保存最终答案快照", "存在未分类的记录缺口"),
        )
        self.assertIsInstance(coverage_reason_labels([]), tuple)

    def test_all_known_reason_labels_are_localized(self):
        expected = {
            "missing_final_snapshot": "未保存最终答案快照",
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
            "missing_item_entered": "未记录进入题目",
            "final_snapshot_mismatch": "最终答案快照与正式提交不一致",
            "submission_chain_mismatch": "正式提交与作答关闭记录不一致",
            "legacy_contract": "历史记录没有当前采集契约",
        }
        for reason, label in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(coverage_reason_labels([reason]), (label,))

    def test_event_time_sources(self):
        self.assertEqual(event_time_source_message("synchronized_estimate"), "同步估计时间")
        self.assertEqual(event_time_source_message("server_received_at"), "服务器接收时间")
        self.assertEqual(event_time_source_message("unknown"), "时间来源无法判断")
        self.assertEqual(event_time_source_message("future_source"), "时间来源无法判断")
