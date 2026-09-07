# 2026-09-07 导出包内容、冻结与可复现性核查

## 核查边界

本次以 `test_kt_e2e` 的隔离 PostgreSQL 数据库构造一名已登记实验参与者、已提交和待评分链、有效评分、0% Confidence、仿真快照、capture-v2 Coverage 运行、时间同步 0 值与空值、分支响应及服务器观察到的 409 冲突。未写入 `kt_platform`、`kt_e2e` 或任何真实数据文件。

实际开发库的只读盘点显示：`kt_platform` 有 93 道演示题，但没有 session、attempt 或 `AssessmentEvent`。唯一已完成 complete 导出为 `3f2fd3fa-2e6f-4269-9320-3d2413777a12`；其 ZIP SHA-256 与数据库记录一致，CRC 通过，46 个成员中 43 个 CSV 的 manifest 行数、实际行数和行字段数均一致，所有数据表均为 `no_observation`。这只能证明该空包自洽，不能用作真实学生记录完整性的证据。

## 实际生成矩阵

| 导出物 | 实际生成与内容核验 | 冻结/可复现核验 |
| --- | --- | --- |
| 标准研究 ZIP：`complete`、`kt`、`cd`、`process`、`confidence`、`simulation`、`longitudinal` | 每种均实际生成。逐表检查 catalog 成员、CSV 表头、行数、manifest 行数、冻结表 SHA-256 和 ZIP CRC。`confidence` 中合法的概率 `0` 保留为 `0`。 | 所有共享 `attempts.csv` 均来自同一冻结源；完成包含独立 replay 脚本。 |
| 标准教学 XLSX：`teaching` | 实际生成并以 openpyxl 读取 `教学成绩`、`导出血缘` 两张表。教学表包含身份和有效评分字段，不包含完整 `final_answer`。 | 血缘表写入冻结 snapshot 标识和截止信息。教学包是约定的教学子集。 |
| 身份 ZIP | 实际生成 `01_identity/student_roster.csv`，核验冻结 roster 中的一名已登记实验参与者。 | manifest 仅选择 `student_roster`。 |
| 自定义 ZIP | 实际生成 `attempts` + `recording_coverage_runs`；核验成员精确为所选 CSV，未错误附带 complete 的 replay 脚本。 | 所选表字节来自同一不可变 source bundle。 |
| 遗留直接 CSV/XLSX | `IdentifiedExportTests` 定向运行了 identified XLSX、identified navigation CSV、legacy identity/KT/navigation URL 与固定表头路径。 | 路径兼容、身份字段和审计记录已验证。 |
| 单会话 Word DOCX | 实际 POST 生成并用 python-docx 读取。核验正式答案、待评分文案、所有者范围和最小审计日志；报告不包含导航、用时、解析等非展示字段。 | Word 是展示型会话报告，不声称是完整研究归档。 |

`test_all_advertised_export_artifacts_materialize_frozen_catalog_members` 额外在 source snapshot 创建后修改 live draft，再次导出 complete；冻结的 `attempts.csv` 字节保持一致。独立的漂移和截止边界回归分别验证了身份/题干/draft 后续变化不会回流，以及 cutoff 后提交不会进入冻结前快照。

## 已修复的有证据缺陷

1. `grade_records.csv` 追加了 `grader_version`、`voided_by_id`、`created_at`。历史评分链现在可以恢复评分器版本、撤销者和写入顺序；旧列位置未改变。
2. complete 和 process 归档新增 `07_quality/recording_coverage_runs.csv`。它冻结 scope、algorithm/code version、完整 parameter document/hash、capture contract hashes、source cutoff/high-water marks/source hash，而不是只留下一个需要回查当前数据库的 run ID。
3. capture-v2 文本编辑 Coverage 升为 `coverage-v3` / `recording-coverage-v3`。正式分析边界内每个文本 `response_transition.payload.operation_id` 必须与同 attempt 的有效 `input_operation` 一一对应；多个 IME 原始通知可共享一个 operation ID，但两个状态转换不能复用它。缺失、无效、跨 attempt 或复用均为 partial，并保存具体原因。新版本和参数进入 frozen run 的 source hash，旧 run 不会被当作新算法缓存复用。
4. `input_operation` 加入 curated `response_events.csv`，同时保留完整 `assessment_events.csv`。字段字典明确 `response_input_observations.csv` 是旧协议稳定观测表，capture-v2 的 IME/输入操作应分析 `response_events` 或全量 raw events。
5. 服务器观察到的 `response_pointer_conflict_observed` 同时出现在 raw 和 curated response 投影。字典说明每行是一例服务器观察到的 409，不是去重后的冲突发生率，且不含答案正文。
6. `client_time_syncs.csv` 以 `is not None` 区分缺失与有效零：`client_received_monotonic_ms=0.000`、时钟偏移 `0` 和 RTT `0` 不再导出为空；`None` 仍为空。同行检查确认 client sequence 强制从 1 开始，限时配置中的 0 在服务层已语义化为无时限，因此未做泛化的 `or ""` 改动。
7. 字典明确 `branch_id` 是客户端 trajectory 实例 token，可随页面/实例变化；响应路径的权威关系是 parent-event DAG、状态哈希、active/final pointer 与显式 branch resolution。

## 验证证据

在 `POSTGRES_DB=kt_e2e` 上运行：

```text
manage.py test \
  apps.analytics.test_capture_v2_coverage.CaptureV2TextInputCoverageTests \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_grade_record_history_freezes_provenance_and_original_comment \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_complete_export_freezes_coverage_run_provenance \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_client_time_sync_export_distinguishes_zero_from_missing_measurements \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_all_advertised_export_artifacts_materialize_frozen_catalog_members \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_source_bundle_keeps_csv_bytes_after_live_draft_identity_and_question_drift \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_source_bundle_keeps_a_pre_cutoff_attempt_after_later_submission \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_complete_export_freezes_question_content_and_capture_v2_replay \
  --keepdb --noinput
```

结果为 **14 tests, OK**。最后一项实际执行 complete 包内的独立 `replay_response_trajectory.py`，并验证篡改 trajectory 或 raw event 后会失败。

Word 与遗留格式另行运行：

```text
manage.py test \
  apps.experiments.test_session_reports.CourseExperimentSessionReportTests.test_word_report_contains_only_report_fields_and_a_privacy_safe_audit_log \
  apps.experiments.test_session_reports.CourseExperimentSessionReportTests.test_word_report_keeps_the_formal_answer_when_grading_is_pending \
  apps.experiments.test_session_reports.CourseExperimentSessionReportTests.test_word_route_reuses_owner_scope_for_downloads \
  apps.analytics.tests.IdentifiedExportTests.test_excel_export_forces_student_identity_and_primary_question_code \
  apps.analytics.tests.IdentifiedExportTests.test_identified_navigation_contains_original_identity_and_audit \
  apps.analytics.tests.IdentifiedExportTests.test_legacy_urls_export_direct_identity_and_record_audit \
  apps.analytics.tests.IdentifiedExportTests.test_legacy_csv_urls_keep_paths_but_use_identified_headers_and_snapshot_code \
  --keepdb --noinput
```

结果为 **7 tests, OK**。

## 已知观测边界

* 客户端看到 draft-save 409 后离线或放弃、从未完成服务器请求的情形不会产生服务器观察事件。因此 `response_pointer_conflict_observed` 不能估计完整的客户端冲突发生率。
* 当前真实开发库没有作答原始证据；本报告不补造它。实际学生记录的内容完整性需要在未来包含真实观测的、经授权的冻结 snapshot 上重复同一成员/行数/哈希/replay 检查。


## 本轮导出边界补充（2026-09-07）

独立的 `apps.analytics.test_export_edge_audit` 在隔离 Django
`test_kt_e2e` 数据库中先复现了三项导出契约缺口；没有直接写入、创建或
flush 实际 `kt_e2e`，也没有触及 `kt_platform`。

1. `kt`、`cd` 和 `teaching` 的“单一实验版本”约束此前只在调用方提供
   `DataScope` 时执行。服务层无 scope 调用可以把同一实验的多个版本冻结进
   `kt` 包，使版本内题目编码、KC 映射和提交顺序被混在一个专用包中。现在在
   服务端已解析的 `selected_versions` 上再次检查：这三类专用包必须恰有一个
   冻结版本。
2. 教学 XLSX 固定从 `attempts` 的冻结行生成成绩投影，但可携带
   `selected_tables=["sessions"]`。旧行为仍写出 attempts 投影，却让 manifest
   声称选择了 sessions。现在教学包只接受未指定表或显式的
   `["attempts"]`；其他表选择在写入任何 artifact 前拒绝。
3. `ExportJob.selected_fields` 被保存和路由传递，但固定 CSV 架构从未按它
   投影；请求较小字段集仍会得到完整表（包括未请求的字段）。这既不能表示
   字段最小化，也会破坏需要完整列集合的引用与 replay 契约。现阶段服务端
   明确拒绝字段级选择，而不是静默输出完整表。旧的独立 identified XLSX
   字段选择路径不经 `ExportJob`，不受此限制。

这不是把所有专用包强行扩展为 complete。`kt`、`cd`、`confidence`、
`simulation`、`longitudinal` 与教学包都是有意的、用途受限的表子集；本轮未
把“缺少 complete 的表”自动判为缺陷。完整的 response replay 仍只由具有其
全部冻结依赖和校验脚本的 complete 包声明；有效的自选表子集可有意不包含
replay 资源，且 manifest 的 `selected_tables` 必须与实际物一致。

先运行的新测试在修复前为 **3 failures**（均为预期的“未拒绝”行为），修复
后运行：

```text
manage.py test \
  apps.analytics.test_export_edge_audit \
  apps.analytics.test_export_scope_v2.ExportScopeV2Tests.test_kt_and_cd_reject_all_version_scope \
  apps.analytics.test_export_scope_v2.ExportScopeV2Tests.test_teaching_package_is_a_single_version_xlsx_with_lineage \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_source_bundle_keeps_csv_bytes_after_live_draft_identity_and_question_drift \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_source_bundle_keeps_a_pre_cutoff_attempt_after_later_submission \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_kt_sequences_only_include_scored_formal_submissions \
  apps.analytics.test_export_lineage_v2.ExportLineageV2Tests.test_complete_export_freezes_question_content_and_capture_v2_replay \
  --keepdb --noinput
```

结果为 **9 tests, OK**；另行运行 `manage.py check`，结果为 **0 silenced**。
未重复仅覆盖七种广告包成员生成的测试；本轮只验证新增边界、冻结漂移、截止后
提交排除、正式 KT 行和独立 replay。


## 第一轮集成与浏览器记录核验（保留当时证据）

- 完整 PostgreSQL 后端回归：**1,155/1,155 通过，816.465 秒**。日志：`frontend/.e2e-results/export-audit-backend-full.log`。本轮文本操作完整性算法版本为 `coverage-v3`。
- 五类题型真实浏览器自动化完成后，从该隔离 `kt_e2e` session 创建新完整导出包，而不是直接在测试中拼装其事件。包内包含 1 session、5 attempts、5 正式提交、157 原始事件、28 次答案状态转换和 21 个内容快照；重复正文没有合并对应行为。
- 仅使用导出包内的 `06_derived/replay_response_trajectory.py`，在独立目录并移除 PostgreSQL/Django 环境变量后执行：`response replay validated`。全部答案路径与正式冻结引用通过校验；同一快照重跑的所有 CSV SHA-256 一致。
- 浏览器包及验证结果：`frontend/.e2e-results/browser-export-replay/complete.zip`、`result.json`。这是隔离演示数据，不能称为真实学生研究数据。
- 该 session 的答案链与正式答案均为 complete，整体 Coverage 仍为 partial。具体原因必须分开解释：最后页面 checkpoint 的 emitted=20、contiguous ACK=19、pending=1，但服务端原始表已收到该实例的连续 1–22 条事件；现有保守规则仍报告 tail_gap，表示客户端尾部确认不足，**不等于已证明原始事件丢失**。离线刷新后的简答实例另有 time_sync_unavailable。保留原始 checkpoint、时间标记和既有判定，不把可重放状态路径冒充所有时间/传输证据完整。
- 因而本次可验证结论是：已纳入冻结包的记录能够独立重放、质量限制可追踪、标准包内容与冻结来源一致。不能将整体 partial 的 session 声称为无缺失或精确时间证据完整。

## 第二轮详细复查与最终验证

本轮继续在 `feat/short-response-capture-v2` worktree 操作，未提交、合并或改动 main，未修改真实题库。仅为浏览器回归重建了明确隔离的 `kt_e2e` 夹具；Django 回归使用独立的 `test_kt_e2e`。

### 发现与修复

- 导出边界除上文三项外，还补齐物化阶段校验：即使先创建未指定包类型的多版本快照，随后改选 KT/CD/教学包，`run_export_job` 也依据冻结的 `selected_versions` 拒绝生成混合版本的专用包。
- 采集器的有界上传曾早于异步本地持久化完成；后台上传收尾时新产生的反馈事件也可能未被导航前的等待覆盖。现在等待已排队记录及正在进行的上传，再发送剩余事件；最后的 ACK 检查点继承 keepalive。
- 退出题目会等待有界上传，反馈产生后的导航也会执行收尾。Vue 释放题目运行时同时停止旧采集器的同步/上传定时器。未更改事件协议、IndexedDB 名称/版本、答案状态或正式提交语义。
- 待评分曾在 `analysis_eligibility.outcome` 中被误报为 `submission_chain_mismatch`。现在明确为 `grading_pending` / `effective_grade_unavailable`，尚不可用于成绩分析；真正的权威答案矛盾仍为 invalid 且不可用。
- 手机题目编辑页 KC 映射表的控件挤压已修复为表格容器内横向滚动，详见 `docs/ui/UI-AUDIT.md`。没有改变 KC 编辑、保存或校验规则。

### 验证结果

| 验证 | 本轮结果 |
|---|---|
| PostgreSQL 完整后端回归 | 1,158/1,158，900.681 秒；`recheck-backend-full.log` |
| 全套启动后新增的导出版本及待评分修复，加既有 Coverage/冻结导出回归 | 52/52，30.786 秒；`recheck-final-targeted.log` |
| 采集器 Node 回归 | 28/28；三项竞态用例先失败后通过 |
| 前端完整单元回归 | 177/177；受影响运行时的 21 项包含导航等待与释放检查 |
| 五题型浏览器重新作答 | 2/2，含组合输入、离线刷新重连、正式锁定、待评分和反馈暴露 |
| 展开状态布局 | 4/4；修正截图定位后另重拍长文本编辑页，1/1 |
| 工程检查 | lint 无错误（2 项既有测试夹具警告）；API 契约通过（13 项既有 schema 警告）；Node 24 build/typecheck、Django check、迁移一致性、collectstatic 通过 |

日志和截图均位于 `frontend/.e2e-results/`。API 检查必须带 PostgreSQL 环境变量：缺失时默认数据库的整数字段范围会令 schema 比较漂移；本轮正确环境下重新检查通过，没有为绕过检查而改写生成类型。

### 实际浏览器记录与冻结包

最终包为 `frontend/.e2e-results/browser-export-final/complete.zip`，机器可读核验结果为同目录 `result.json`。该包来自本轮浏览器真实执行生成的隔离测试记录：1 session、5 attempts、5 正式提交、157 原始事件、28 次答案转换、21 正文快照、5 评分记录。4 条正式 KT 序列对应已评分题；简答待评分未自动记零或记错。

独立子进程仅使用包内重放脚本，移除数据库/Django 环境变量后返回 `response replay validated`；ZIP CRC、文件 SHA-256、同一冻结快照重跑的所有 CSV SHA-256 均通过。正式答案和待评分状态由导出包即可核验。前一轮和本轮修正质量原因前的包保留在各自目录，没有被新包覆盖。

### 尚不能宣称“所有证据完整”的边界

1. 最后一题已由上一轮 emitted=20 / ACK=19 / pending=1 改善为 emitted=22 / ACK=22 / pending=0；本轮新增上传收尾回归有实际浏览器证据。
2. 另一个快速离开的客户端仍只有旧 resume 检查点（emitted=3 / ACK=0 / pending=3），但原始表已连续收到该实例 1–3 条事件。这是客户端确认未收尾，不能称为已证明答案丢失，也不能伪造新检查点。本轮保留 `tail_gap`；既有会话级保守标记会传播到题目回放资格。
3. 离线刷新后部分事件没有可用的时间同步证据，仍标记 `time_sync_unavailable`。可用事件顺序与精确的跨客户端时间不是同一保证。
4. 因此整体仍为 `partial`。独立状态重放通过只能证明包内已记录路径及正式结果的一致性；不能证明从未发送的事件不存在。浏览器自动化的组合输入验证也不替代真实中文输入法和手机软键盘实机验证。

当前审查支持的结论是：测试覆盖的功能记录及标准包生成/冻结/重放路径正常，确认的缺陷已修复，剩余证据限制可在包内识别。完整科研归档应选择 complete 包；各专用包、教学 XLSX 和自选子集只对其声明的数据范围负责，不是平台全部记录的替代品。
