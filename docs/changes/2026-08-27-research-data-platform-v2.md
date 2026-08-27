# 研究行为数据平台 v2：字段级冻结说明

本次 rebuild 冻结的是 B 层事实契约，不把 hesitation、KT、CD 或能力解释写入采集层。

## 权威边界

- `Attempt.current_response_snapshot_id` / `final_response_snapshot_id` 与 `SubmissionEvent.final_response_snapshot_id` 是文本和提交状态的权威指针；`draft_answer`、`final_answer`、`Attempt.score`、`Attempt.is_correct` 仅为兼容投影。
- `response_committed` 只证明服务器接受了最终答案，包含 `submission_id`、`submission_token`、最终快照引用、提交时间和 `grading_status`，不把分数或正误作为不可变提交事实。
- `GradeRecord` 以 `(submission, version)` 追加评分版本，保留 `score`、`max_score`、`is_correct`、`normalized_response`、`grading_method`、`grading_version` 和 `supersedes_grade`。
- `ResponseState` 是不含 UUID 的逻辑状态；`ResponseStateRef` 才引用具体快照。`state_hash` 对 canonical JSON 计算，绝不把 `snapshot_id` 纳入哈希。
- 数值、填空和简答保留学生的 raw lexical input；trim、大小写、Unicode 或数值归一化只允许发生在评分派生中。

## 文本输入粒度

`ResponseInputObservation` 只存稳定 input 的编辑类型、前后 hash、长度、输入方式和客户端时钟，不存正文。IME composition 中间态不写入；完整正文只在 debounce burst end、blur/hidden、离题、paste/large replace、pagehide 和 submit 等检查点写入 `ResponseSnapshot`。

因此简答题的承诺是“稳定输入事件级行为重放 + 检查点级文本状态重建”，不是逐字符 keylogger。

## 时间与顺序

同一 `client_instance_id` 内，`client_sequence_no` 严格递增且 `client_monotonic_ms` 不回退。跨实例不直接相减；必须依赖时间同步后的 UTC 估计，并将区间标记为 `synchronized_estimate`、`degraded` 或 `unknown`。服务端 `server_ingest_sequence_no` 只用于提交顺序和导出高水位，不替代客户端发生顺序。

## 实验分配

`ExperimentVersion.assignment_unit_type` 声明 subject/session 单位。`ConditionAssignment` 保留算法、版本、随机种子、随机抽样值、分层和 assignment probability；Session 的 condition 绑定后不可变。单 Attempt 使用 writer lease，其他标签页只能读或被拒绝写入。

## 导出与治理

`ExportSnapshot` 在创建时冻结 `source_cutoff_at`、`event_high_watermark`、`schema_manifest_hash` 和筛选定义。导出事务只读取该边界内的记录，manifest 和 `derivation_runs.json` 记录 schema、派生版本、代码提交和来源模型。撤回、隐私删除或法务 purge 是 append-only 正常生命周期约束之外的明确例外；相关 ExportSnapshot/ExportJob 标记为 invalidated。

## 五个机器不变量

1. Identity：事件的 Attempt、Session、Assignment 互相一致。
2. State chain：相邻响应事件满足 `after[n] == before[n+1]`，除非有显式恢复标记。
3. Submission：提交事件、Submission 和 Attempt 的最终快照指针一致。
4. Ordering/time：同一客户端序号递增、单调时间不回退；跨客户端不伪装为精确间隔。
5. Provenance：每个导出/派生结果可追溯到 ExportSnapshot、schema manifest、derivation version 和 source records。
