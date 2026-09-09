import json

from django.test import SimpleTestCase


EVENT_HEADERS = (
    "student_number",
    "student_name",
    "question_code",
    "event_id",
    "event_type",
    "session_id",
    "attempt_id",
    "client_instance_id",
    "client_sequence_no",
    "server_ingest_sequence_no",
    "server_received_at",
    "payload",
    "question_snapshot_id",
    "client_wall_time_utc",
    "estimated_occurred_at_utc",
    "derived_occurred_at_utc",
    "server_committed_at",
    "time_quality",
)


def event_row(
    event_id,
    event_type,
    sequence,
    payload,
    *,
    session_id="11",
    attempt_id="21",
    question_snapshot_id="31",
    client_instance_id="browser-1",
    client_sequence_no=None,
    server_received_at=None,
    client_wall_time_utc="",
    derived_occurred_at_utc="",
    time_quality="exact_monotonic",
):
    return [
        "raw-student-number",
        "raw-student-name",
        "RAW-Q",
        event_id,
        event_type,
        session_id,
        attempt_id,
        client_instance_id,
        client_sequence_no if client_sequence_no is not None else sequence,
        sequence,
        server_received_at or f"2026-09-09T08:00:{sequence:02d}+00:00",
        json.dumps(payload, ensure_ascii=False),
        question_snapshot_id,
        client_wall_time_utc,
        "",
        derived_occurred_at_utc,
        "",
        time_quality,
    ]


def state(question_type, value):
    return {"question_type": question_type, "value": value}


class ResearchBehaviorProjectionTests(SimpleTestCase):
    maxDiff = None

    def test_session_event_does_not_inherit_unanswered_assignment_question(self):
        event = event_row("page", "window_blurred", 1, {}, attempt_id="", question_snapshot_id="")
        event[2] = ""
        tables = {
            "attempts": (("attempt_id", "session_id", "question_snapshot_id"), [[None, "12", "32"]]),
            "assessment_events": (EVENT_HEADERS, [event]),
        }
        rows = self.rows_as_dicts(tables)
        self.assertFalse(rows[0]["题目编号"])

    def test_answer_formatter_preserves_text_exactly_and_distinguishes_zero_from_missing(self):
        from .research_events import format_answer_value

        self.assertEqual(format_answer_value("  0  "), "  0  ")
        self.assertEqual(format_answer_value("null"), "null")
        self.assertEqual(format_answer_value('"abc"'), '"abc"')
        self.assertEqual(format_answer_value("第一行\n第二行"), "第一行\n第二行")
        self.assertEqual(format_answer_value(0), 0)
        self.assertIsNone(format_answer_value(None))
        self.assertEqual(format_answer_value({"question_type": "short_answer", "value": "  答案  "}), "  答案  ")
        self.assertEqual(format_answer_value({"question_type": "multiple_choice", "value": ["B", "A"]}), '["B", "A"]')

    def rows_as_dicts(self, tables, *, session_refs=None, question_refs=None, identity=None):
        from .research_events import BEHAVIOR_HEADERS, build_behavior_rows

        rows = build_behavior_rows(
            tables,
            session_refs=session_refs or {"11": "会话-001"},
            question_refs=question_refs or {"31": "题目-01", "32": "题目-02"},
            identity_by_session=identity or {"11": ("S001", "李同学")},
        )
        return [dict(zip(BEHAVIOR_HEADERS, row)) for row in rows]

    def test_projects_every_transition_and_keeps_input_operations_separate(self):
        transitions = [
            ("e1", None, None, "A", "select", "A"),
            ("e2", "e1", "A", "B", "select", "B"),
            ("e3", "e2", "B", "A", "select", "A"),
            ("e4", "e3", "A", "A", "select", "A"),
        ]
        rows = []
        for sequence, (event_id, parent, before, after, action, option) in enumerate(transitions, 1):
            rows.append(
                event_row(
                    event_id,
                    "response_transition",
                    sequence,
                    {
                        "operation_id": f"op-{sequence}",
                        "parent_event_id": parent,
                        "before_state": state("single_choice", before),
                        "after_state": state("single_choice", after),
                        "input_type": "change",
                        "option_key": option,
                        "option_action": action,
                        "before_state_hash": "a" * 64,
                        "after_state_hash": "b" * 64,
                    },
                )
            )
        rows.extend(
            [
                event_row(
                    "e5",
                    "response_transition",
                    5,
                    {
                        "operation_id": "op-5",
                        "parent_event_id": "e4",
                        "before_state": state("multiple_choice", ["A", "B"]),
                        "after_state": state("multiple_choice", ["A"]),
                        "input_type": "change",
                        "option_key": "B",
                        "option_action": "deselect",
                    },
                ),
                event_row(
                    "e6",
                    "input_operation",
                    6,
                    {
                        "operation_id": "op-6",
                        "parent_event_id": "e5",
                        "input_type": "insertCompositionText",
                        "composition": "confirmed",
                        "selection_start": 2,
                        "selection_end": 2,
                    },
                ),
                event_row(
                    "e7",
                    "response_transition",
                    7,
                    {
                        "operation_id": "op-6",
                        "parent_event_id": "e5",
                        "before_state": state("short_answer", "第一行\n旧字"),
                        "after_state": state("short_answer", "第一行\n新字"),
                        "input_type": "insertCompositionText",
                        "option_key": None,
                        "option_action": None,
                    },
                ),
            ]
        )
        tables = {
            "assessment_events": (EVENT_HEADERS, list(reversed(rows))),
            # Same transport event is present in a second frozen source table.
            "response_events": (EVENT_HEADERS, [rows[1]]),
        }

        projected = self.rows_as_dicts(tables)

        transitions_out = [row for row in projected if row["具体行为"] == "答案状态变化"]
        self.assertEqual(
            [(row["变化前答案"], row["变化后答案"]) for row in transitions_out[:4]],
            [(None, "A"), ("A", "B"), ("B", "A"), ("A", "A")],
        )
        for choice_transition in transitions_out[:4]:
            self.assertIsNone(choice_transition["输入内容"])
            self.assertIsNone(choice_transition["删除内容"])
        self.assertEqual(len(transitions_out), 6)
        deselect = transitions_out[4]
        self.assertEqual(deselect["操作选项"], "B")
        self.assertEqual(deselect["变化前答案"], '["A", "B"]')
        self.assertEqual(deselect["变化后答案"], '["A"]')
        self.assertIsNone(deselect["输入内容"])
        self.assertIsNone(deselect["删除内容"])
        text_change = transitions_out[5]
        self.assertEqual(text_change["删除内容"], "旧")
        self.assertEqual(text_change["输入内容"], "新")
        self.assertEqual(text_change["行为内容"], "由相邻答案状态计算的净变化")

        input_operation = next(row for row in projected if row["具体行为"] == "输入操作")
        self.assertIn("输入法确认", input_operation["行为内容"])
        self.assertIn("输入类型：输入法组合文本", input_operation["行为内容"])
        self.assertIsNone(input_operation["输入内容"])
        self.assertIsNone(input_operation["删除内容"])
        self.assertEqual([row["步骤序号"] for row in projected], list(range(1, 8)))

    def test_projects_each_visit_submit_confidence_feedback_tool_and_page_state(self):
        source_headers = ("assignment_id", "question_snapshot_id")
        events = [
            event_row(
                "n2",
                "item_entered",
                2,
                {"entry_reason": "navigation_click", "from_assignment_id": 101},
                question_snapshot_id="32",
                client_wall_time_utc="2026-09-09T07:59:58+00:00",
                time_quality="client_wall_unsynchronized",
            ),
            event_row(
                "n1",
                "item_entered",
                1,
                {"entry_reason": "session_start", "from_assignment_id": None},
                derived_occurred_at_utc="2026-09-09T07:59:50+00:00",
                time_quality="synchronized_estimate",
            ),
            event_row("n3", "item_rendered", 3, {}),
            event_row("n4", "item_left", 4, {"leave_reason": "navigation"}),
            event_row("c1", "confidence_committed", 5, {"probability": 0}),
            event_row(
                "f1",
                "feedback_exposure",
                6,
                {
                    "feedback_mode": "full",
                    "stage": "explanation_presented",
                    "included_correctness": True,
                    "included_correct_answer": True,
                    "included_explanation": True,
                },
            ),
            event_row(
                "t1",
                "research_tool",
                7,
                {"tool": "hint", "stage": "presented", "initiation_type": "self_initiated"},
            ),
            event_row("p1", "document_hidden", 8, {}),
            event_row("p2", "window_focused", 9, {}),
            event_row("s1", "response_committed", 10, {"grading_status": "pending"}, client_instance_id=""),
            event_row("ignored", "network_offline", 11, {}),
            event_row("ignored-2", "response_commit_rejected", 12, {}),
        ]
        tables = {
            "assessment_events": (EVENT_HEADERS, events),
            "assignments": (source_headers, [[101, 31], [102, 32]]),
        }

        projected = self.rows_as_dicts(tables)

        self.assertEqual(sum(row["具体行为"] == "进入题目" for row in projected), 2)
        second_visit = next(row for row in projected if row["题目编号"] == "题目-02")
        self.assertEqual(second_visit["来源题目"], "题目-01")
        self.assertEqual(second_visit["目标题目"], "题目-02")
        self.assertEqual(second_visit["行为内容"], "点击导航")
        self.assertEqual(second_visit["发生时间"], "2026-09-09T08:00:02+00:00")
        self.assertEqual(second_visit["时间依据"], "服务器接收时间（客户端时钟未同步）")
        first_visit = projected[0]
        self.assertEqual(first_visit["发生时间"], "2026-09-09T07:59:50+00:00")
        self.assertEqual(first_visit["时间依据"], "同步估计时间")

        confidence = next(row for row in projected if row["具体行为"] == "提交信心判断")
        self.assertEqual(confidence["操作选项"], 0)
        self.assertIn("0%", confidence["行为内容"])
        self.assertEqual(
            {row["具体行为"] for row in projected},
            {
                "进入题目",
                "呈现题目",
                "离开题目",
                "提交信心判断",
                "呈现反馈",
                "使用研究工具",
                "页面不可见",
                "窗口获得焦点",
                "提交答案",
            },
        )
        exported_text = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn("raw-student", exported_text)
        self.assertNotIn("ignored-2", exported_text)

        feedback = next(row for row in projected if row["具体行为"] == "呈现反馈")
        self.assertIn("反馈模式：完整反馈", feedback["行为内容"])
        self.assertIn("阶段：呈现解析", feedback["行为内容"])
        tool = next(row for row in projected if row["具体行为"] == "使用研究工具")
        self.assertEqual(tool["操作选项"], "查看提示")
        self.assertIn("阶段：已呈现", tool["行为内容"])
        self.assertIn("发起方式：学生主动", tool["行为内容"])
        submission = next(row for row in projected if row["具体行为"] == "提交答案")
        self.assertEqual(submission["行为内容"], "评分状态：待评分")

    def test_marks_unadopted_branches_chain_gaps_and_unrecoverable_legacy_text(self):
        events = [
            event_row(
                "head",
                "response_transition",
                2,
                {
                    "parent_event_id": "missing-parent",
                    "before_state": state("single_choice", None),
                    "after_state": state("single_choice", "A"),
                    "option_key": "A",
                    "option_action": "select",
                },
            ),
            event_row(
                "branch",
                "response_transition",
                3,
                {
                    "parent_event_id": None,
                    "before_state": state("single_choice", None),
                    "after_state": state("single_choice", "B"),
                    "option_key": "B",
                    "option_action": "select",
                },
            ),
            event_row(
                "legacy-text",
                "response_state_changed",
                4,
                {
                    "question_type": "short_answer",
                    "response_snapshot_id": "missing-snapshot",
                    "content_length": 0,
                    "change_source": "student",
                },
            ),
        ]
        replay_headers = (
            "session_id",
            "attempt_id",
            "active_response_event_id",
            "final_response_event_id",
            "active_replay_status",
            "active_replay_chain",
            "final_replay_status",
            "final_replay_chain",
        )
        replay_rows = [
            [
                11,
                21,
                "head",
                "",
                "missing_transition",
                json.dumps([{"event_id": "head"}]),
                "not_selected",
                "[]",
            ]
        ]
        tables = {
            "assessment_events": (EVENT_HEADERS, events),
            "response_replay": (replay_headers, replay_rows),
        }

        projected = self.rows_as_dicts(tables)

        head = next(row for row in projected if row["操作选项"] == "A")
        branch = next(row for row in projected if row["操作选项"] == "B")
        legacy = next(row for row in projected if row["具体行为"] == "答案状态变化（旧采集）")
        self.assertEqual(head["答案记录状态"], "采用链（记录不完整）")
        self.assertEqual(head["记录问题"], "答案变化链缺少上游记录")
        self.assertEqual(branch["答案记录状态"], "未采用分支（记录链不完整）")
        self.assertEqual(legacy["变化后答案"], "")
        self.assertEqual(legacy["记录问题"], "缺少答案快照，无法还原文本")

    def test_projects_protocol_enums_to_chinese_without_rewriting_free_text(self):
        events = [
            event_row("i1", "response_input_started", 1, {"question_type": "short_answer"}),
            event_row(
                "i2",
                "text_edit_observed",
                2,
                {
                    "edit_operation": "paste",
                    "content_length_before": 0,
                    "content_length_after": 4,
                    "input_method": "ime",
                },
            ),
            event_row(
                "i3",
                "confidence_observed",
                3,
                {"stage": "responded", "probability": 80, "feedback_phase": "before_feedback"},
            ),
            event_row(
                "i4",
                "confidence_skipped",
                4,
                {"reason_code": "student_skipped"},
            ),
            event_row(
                "i5",
                "feedback_shown",
                5,
                {"feedback_mode": "correctness"},
            ),
            event_row(
                "i6",
                "explanation_shown",
                6,
                {"source": "review"},
            ),
            event_row(
                "i7",
                "research_tool",
                7,
                {"tool": "doubt", "stage": "mark_added", "initiation_type": "task_required"},
            ),
            event_row(
                "i8",
                "research_tool",
                8,
                {"tool": "future_tool", "stage": "future_stage", "initiation_type": "future_source"},
            ),
        ]

        projected = self.rows_as_dicts({"assessment_events": (EVENT_HEADERS, events)})
        by_step = {row["步骤序号"]: row for row in projected}

        self.assertEqual(by_step[1]["行为内容"], "简答题")
        self.assertEqual(by_step[2]["行为内容"], "编辑类型：粘贴；长度：0→4；输入方式：输入法")
        self.assertEqual(by_step[3]["行为内容"], "信心值：80%；阶段：已作答；反馈阶段：反馈前")
        self.assertEqual(by_step[4]["行为内容"], "跳过原因：学生跳过")
        self.assertEqual(by_step[5]["行为内容"], "反馈模式：仅正误反馈")
        self.assertEqual(by_step[6]["行为内容"], "来源：复习")
        self.assertEqual(by_step[7]["操作选项"], "标记疑问")
        self.assertEqual(by_step[7]["行为内容"], "工具：标记疑问；阶段：添加标记；发起方式：任务要求")
        unknown = json.dumps(by_step[8], ensure_ascii=False)
        self.assertIn("未识别研究工具", unknown)
        self.assertIn("未识别研究工具阶段", unknown)
        self.assertIn("未识别发起方式", unknown)
        self.assertNotIn("future_tool", unknown)
        self.assertNotIn("future_stage", unknown)
        self.assertNotIn("future_source", unknown)

    def test_preserves_clear_transition_and_repeated_navigation(self):
        events = [
            event_row(
                "clear",
                "response_transition",
                1,
                {
                    "parent_event_id": None,
                    "before_state": state("single_choice", "A"),
                    "after_state": state("single_choice", None),
                    "input_type": "change",
                    "option_key": "A",
                    "option_action": "clear",
                },
            ),
            event_row("v1", "item_entered", 2, {"entry_reason": "session_start", "from_assignment_id": None}, question_snapshot_id="31"),
            event_row("v2", "item_entered", 3, {"entry_reason": "navigation_click", "from_assignment_id": 101}, question_snapshot_id="33"),
            event_row("v3", "item_entered", 4, {"entry_reason": "navigation_click", "from_assignment_id": 103}, question_snapshot_id="31"),
            event_row("v4", "item_entered", 5, {"entry_reason": "navigation_click", "from_assignment_id": 101}, question_snapshot_id="32"),
        ]
        tables = {
            "assessment_events": (EVENT_HEADERS, events),
            "assignments": (("assignment_id", "question_snapshot_id"), [[101, 31], [102, 32], [103, 33]]),
        }

        projected = self.rows_as_dicts(
            tables,
            question_refs={"31": "Q1", "32": "Q2", "33": "Q3"},
        )

        cleared = projected[0]
        self.assertEqual(cleared["变化前答案"], "A")
        self.assertIsNone(cleared["变化后答案"])
        self.assertEqual(cleared["行为内容"], "清空答案")
        visits = [row for row in projected if row["具体行为"] == "进入题目"]
        self.assertEqual(
            [(row["来源题目"], row["目标题目"]) for row in visits],
            [(None, "Q1"), ("Q1", "Q3"), ("Q3", "Q1"), ("Q1", "Q2")],
        )

    def test_submission_fact_projects_server_time_and_final_snapshot_answer(self):
        tables = {
            "attempts": (
                ("attempt_id", "session_id", "question_snapshot_id"),
                [[21, 11, 31]],
            ),
            "response_snapshots": (
                ("snapshot_id", "attempt_id", "content"),
                [["snapshot-final", 21, "  最终答案\n第二行  "]],
            ),
            "submissions": (
                (
                    "submission_id",
                    "attempt_id",
                    "final_response_snapshot_id",
                    "grading_status",
                    "client_clicked_at",
                    "server_received_at",
                    "committed_at",
                ),
                [[91, 21, "snapshot-final", "completed", "2026-09-09T08:59:58+00:00", "2026-09-09T09:00:00+00:00", "2026-09-09T09:00:01+00:00"]],
            ),
        }

        projected = self.rows_as_dicts(tables)

        self.assertEqual(len(projected), 1)
        submission = projected[0]
        self.assertEqual(submission["具体行为"], "提交答案")
        self.assertEqual(submission["变化后答案"], "  最终答案\n第二行  ")
        self.assertEqual(submission["发生时间"], "2026-09-09T09:00:00+00:00")
        self.assertEqual(submission["时间依据"], "服务器接收时间")
        self.assertEqual(submission["服务器记录时间"], "2026-09-09T09:00:00+00:00")
        self.assertEqual(submission["行为内容"], "评分状态：已评分")

    def test_does_not_use_derived_time_without_matching_synchronization_quality(self):
        row = event_row(
            "time-1",
            "item_rendered",
            1,
            {},
            derived_occurred_at_utc="2026-09-09T07:59:00+00:00",
            time_quality="exact_monotonic",
        )

        projected = self.rows_as_dicts({"assessment_events": (EVENT_HEADERS, [row])})

        self.assertEqual(projected[0]["发生时间"], "2026-09-09T08:00:01+00:00")
        self.assertEqual(projected[0]["时间依据"], "服务器接收时间（客户端单调时钟仅用于排序）")
