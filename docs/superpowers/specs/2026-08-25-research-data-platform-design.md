# KT 平台过程数据采集、去标识化研究导出与数据治理设计

**状态：** 已批准，进入实施  
**日期：** 2026-08-25  
**适用范围：** Django 单体平台、SQLite 便携版、PostgreSQL 部署版

## 1. 目标与非目标

本设计在保留现有业务状态表的前提下，新增不可变过程事件、版本化状态快照和版本化派生数据。系统应完整保留可验证的 Outcome、Process、Exposure、Self-report 与 Simulation 事实，同时避免把 `uncertainty_level`、`hesitation_score`、`careless_error` 或 `misconception` 等理论解释写入原始数据层。

系统不是完整 event sourcing 架构。`ExperimentSession`、`StudentQuestionAssignment`、`Attempt`、`SubmissionEvent` 等现有业务表继续负责业务真值；过程事件负责科研观测。所有包含稳定学生伪名、时间和行为轨迹的数据统一称为“去标识化研究数据”，不称“匿名数据”。

四层数据结构为：

```text
Business Tables
→ Immutable Process Events
→ Versioned State Snapshots
→ Versioned Derived Data
```

普通业务界面不得硬删除已有会话的数据，但系统提供独立、受审计的受控清除流程。身份映射与研究数据导出属于两个安全域，永远不进入同一个 ZIP。

## 2. 不可变过程事件

### 2.1 AssessmentEvent 信封

新增只追加表 `AssessmentEvent`：

```text
event_id UUID
event_domain assessment | telemetry | system
event_type
event_version
envelope_version
actor_source client | server | system

session_id
assignment_id
attempt_id
question_snapshot_id
item_instance_id

client_instance_id
client_sequence_no
server_ingest_sequence_no
correlation_id

client_wall_time_utc
client_monotonic_ms
client_time_origin
server_received_at
server_committed_at

time_sync_id
estimated_clock_offset_ms
estimated_rtt_ms
estimated_occurred_at_utc

capture_mode native | legacy_projection
quality_flags
payload
app_version
```

`client_sequence_no` 只在同一 `client_instance_id` 内构成局部行为顺序。`server_ingest_sequence_no` 只表达服务器持久化顺序，不能解释为学生行为顺序。`correlation_id` 关联客户端命令与服务端成功或拒绝结果。`estimated_occurred_at_utc` 只有在有效时间同步下生成，否则必须为空。

唯一约束为 `event_id`、`(session_id, server_ingest_sequence_no)` 与 `(client_instance_id, client_sequence_no)`。主要索引为 `(session_id, server_ingest_sequence_no)`、`(attempt_id, client_instance_id, client_sequence_no)`、`question_snapshot_id` 以及 `(event_type, server_received_at)`。PostgreSQL 为 `server_received_at` 增加 BRIN 索引；SQLite 使用普通索引。普通应用服务不得更新或删除事件，只有受控 purge 服务可以清除。

### 2.2 Schema registry

每个 `(event_type, event_version)` 对应仓库内一份不可变 JSON Schema，`additionalProperties` 为 `false`。公共信封由独立的 `envelope_version` 管理。payload 字段改名或语义变化必须提升事件版本。服务端在持久化前使用 `jsonschema` 校验；研究包 manifest 列出实际使用的 schema 路径、版本与 SHA-256。

### 2.3 原始事件集合

原始事件只表达系统真实观测，不保存可派生计数。

```text
Session:
  session_started
  session_completed
  session_expired
  session_invalidated

Item lifecycle:
  item_entered
  item_rendered
  item_left

Response:
  response_input_started
  response_state_changed
  response_commit_requested
  response_committed
  response_commit_rejected
  attempt_closed

Exposure:
  feedback_shown
  explanation_shown
  hint_opened
  hint_closed
  resource_opened
  resource_closed

Telemetry:
  document_visible
  document_hidden
  window_focused
  window_blurred
  pageshow
  pagehide
  network_online
  network_offline
  activity_heartbeat
```

`response_commit_requested` 与后续结果共享 `correlation_id`。`response_committed` 必须与 `Attempt`、`SubmissionEvent` 和评分结果在同一事务写入；拒绝事件保存结构化原因码，不保存异常堆栈。`reentered`、`revisit_count`、答案修改次数等由事件序列派生。

heartbeat 默认每 30 秒发送一次，仅在题目页可见且 attempt 未关闭时采集。发布版本允许 15–120 秒配置。payload 只允许 `attempt_id`、`document_visible`、`window_focused` 与 `last_device_interaction_age_ms`。系统不采集逐键内容、鼠标轨迹、剪贴板、浏览历史、摄像头、麦克风、GPS、眼动或 EDA。

### 2.4 时间同步

新增 `ClientTimeSync`：

```text
time_sync_id
client_instance_id
client_wall_time_utc
client_monotonic_ms
client_time_origin
server_received_at
server_responded_at
estimated_clock_offset_ms
estimated_rtt_ms
sync_quality
created_at
```

页面首次加载、恢复网络和每 15 分钟产生同步样本。客户端系统时间突变时建立新客户端实例或同步样本，并标记旧样本失效。跨刷新和跨客户端实例不存在绝对可靠的行为全序；研究分析必须联合使用客户端局部顺序、估计时间和质量标记，且不得把服务器接收时间或估计发生时间描述为无误差真值。

## 3. 版本化状态快照

### 3.1 响应快照

选择题的 `response_state_changed` 保存完整规范化状态与 `question_type`、`change_source`。数值、填空和简答使用 `ResponseSnapshot`：

```text
snapshot_id
attempt_id
content
content_hash
content_length
created_at
schema_version
```

事件只引用 `response_snapshot_id`、hash、长度和变更来源。首次输入产生 `response_input_started`；800 ms 防抖生成快照；blur、离题、导航、跳过和提交前强制快照；同一 attempt 的相同 hash 复用快照；不记录逐键内容。

### 3.2 仿真快照

`SimulationSnapshot` 保存仿真版本、结构化元数据、受保护 blob key、hash、大小、时间和 schema 版本。`simulation_parameter_changed`、`simulation_run_started`、`simulation_run_completed` 与 `simulation_error_returned` 只引用快照；大型电路、波形与文件不进入事件 JSON payload。

### 3.3 呈现实例

在 `QuestionSnapshot` 与 assignment/attempt 之间增加 `ItemInstance`：

```text
item_instance_id
question_snapshot_id
presentation_variant
option_order
parameter_values
random_seed
presentation_hash
created_at
```

静态题也创建轻量默认实例。选项随机、参数随机和动态图片均通过这一层复现学生实际看到的题目版本。

## 4. Confidence 与实验分配

实验配置包含：

```text
confidence_mode off | all | sampled
confidence_sampling_unit student | session | attempt
confidence_probability
randomization_seed
assignment_version
confidence_scale_version
confidence_prompt_version
```

默认 `off`。量表为 0%–100%，步长 10%。`ConfidenceAssignment` 明确保存 condition、分配时间、分配单位、分配版本、prompt 展示时间、响应状态、概率和提交时间。流程为：

```text
response_committed
→ confidence_prompt_shown
→ confidence_committed | confidence_skipped
→ feedback_shown
```

系统必须区分未分配、已分配未展示、展示后跳过、提交成功和事件上传失败，不能通过缺失 Confidence 事件反推实验条件。

## 5. 稳定去标识化学生标识

新增：

```text
CourseSubject(course, student, public_uuid)
ResearchStudy
ResearchStudyCourse
ResearchStudyParticipant(study, student, public_uuid)
```

`course_subject_id` 在同一课程所有实验中稳定、跨课程不同；`study_subject_id` 只在明确研究项目范围内跨课程和学期稳定。两类 ID 均为持久化随机 UUID，不由内部用户 ID 直接计算。旧实验级匿名 ID、URL 和 CSV header 保持兼容并标记为 legacy pseudonym。普通新研究包只含 `course_subject_id`；研究项目包才含 `study_subject_id`。

## 6. 数据治理与安全域

研究数据中心不得展示身份映射下载按钮，只显示“身份关联由课程设置 → 身份与数据治理管理”。独立治理页提供实名核对、课程 ID 映射、研究项目 ID 映射和 purge 管理。课程负责人、课程协作教师可管理其有权访问的课程；管理员可管理全平台；不要求再次输入密码。

身份导出使用独立任务类型、独立受保护目录和独立审计，文件 24 小时过期。研究包保存 7 天。下载只能通过授权视图完成，不能直接暴露 `MEDIA_URL` 路径。

`DataPurgeRequest` 保存范围、原因、请求人、预览计数、状态、执行人、执行时间和结果计数。流程为只读影响预览、确认短语与原因、事务性或分批清除、删除相关导出文件、保留最小审计。审计不得保留被要求删除的原始内容或身份映射。任何包含被清除数据的未过期导出立即失效并删除。

## 7. 课程数据中心

课程“数据”导航升级为：

```text
数据
├─ 数据概览
├─ 采集质量
├─ 实验数据
│  └─ 实验 → 明确版本 → 导出配置
├─ 纵向研究项目
└─ 导出任务
```

身份映射与 purge 位于“课程设置 → 身份与数据治理”。实验结果页只保留学生会话和进入课程数据中心的链接。

实验导出界面采用桌面左树右配置、移动端手风琴：

```text
快速导出
├─ 完整去标识化研究包
├─ 标准 KT 包
├─ CD 包
├─ 作答过程包
├─ Confidence/元认知包
└─ 仿真包

核心表
├─ subjects
├─ sessions
├─ assignments
├─ item_instances
└─ attempts

原始过程
├─ assessment_events
├─ response_events
├─ navigation_events
├─ exposure_events
└─ telemetry_events

测量设计
├─ item_snapshots
├─ kc_dictionary
├─ item_kc_mapping
└─ q_matrix

派生数据
├─ attempt_features
├─ kt_sequences
├─ cd_response_matrix
└─ calibration_features

兼容导出
├─ 旧匿名 KT CSV
├─ 旧匿名导航 CSV
└─ 旧实名可选字段 Excel
```

每张表显示观测粒度、敏感等级、预计行数、主键与关联键。主键和关联键锁定不可取消；字段选择只能发生在同一观测粒度内。新界面统一使用“去标识化”。旧端点、URL 和 header 保持兼容。

## 8. 一致性导出、任务租约与血缘

新增 `ExportSnapshot` 与 `ExportJob`。任务开始时建立只读一致性事务：PostgreSQL 使用 `REPEATABLE READ`，SQLite 使用单一读事务。`export_cutoff_at` 为事务快照建立时间；所有 CSV、JSON、Excel 摘要和派生数据来自同一快照。manifest 保存 snapshot ID、开始与 cutoff 时间、数据库策略、实验版本、每会话最大 ingest sequence、事件 schema hashes 和应用 Git SHA。派生数据只使用同一快照内的原始数据，不重新查询实时数据库。

后台任务保存锁持有者、租约到期时间、heartbeat、重试次数、阶段、产物路径与 hash、完成与过期时间。PostgreSQL 使用 `SELECT ... FOR UPDATE SKIP LOCKED`；SQLite 只允许单 worker 并使用条件更新。worker 每 30 秒续租，租约过期可回收，最多重试 3 次。

研究包结构：

```text
pseudonymized_research_package/
├─ 00_manifest/
├─ 01_design_metadata/
├─ 02_core/
├─ 03_raw_events/
├─ 04_learning_exposure/
├─ 05_self_report/
├─ 06_derived/
└─ 99_audit/
```

身份映射不得进入该目录。每张派生表包含 `derivation_run_id`；`derivation_runs.json` 保存 derivation version、source snapshot/cutoff/schema、代码版本、Git SHA、参数配置及 hash 和生成时间。参数变化必须提升 derivation version。

## 9. 采集质量、历史迁移与规模策略

采集质量页报告客户端序号缺口与重复、接收延迟、时钟未同步、离线上传、中途开始采集、业务提交与 committed 事件不一致、事件不变量违例、事件版本分布、heartbeat 增长速度，以及 native 与 legacy 覆盖率。

历史数据只投影为：

```text
legacy_visit_observed
legacy_draft_observed
legacy_submission_observed
legacy_session_start_observed
legacy_session_end_observed
```

全部使用 `capture_mode=legacy_projection` 和质量 flags。不得伪装为原生事件，不虚构草稿答案、render、leave、focus、visibility、feedback 或 Confidence。部署时正在进行的会话标记 `mid_session_capture`。迁移必须幂等、可重复执行。

第一版保持 SQLite/PostgreSQL 双兼容并使用普通事件表。所有分区相关查询以 `server_received_at` 为时间键。达到 2,000 万事件、20 GB 表大小或单月 500 万事件任一阈值后，以独立运维迁移切换 PostgreSQL 月分区。

## 10. 科研不变量与验收

必须自动验证：

```text
response_committed → 对应 Attempt 与 SubmissionEvent 存在
feedback_shown → 同 attempt 的 response_committed 在行为序列上先发生
attempt_closed(submitted) → 恰有一个有效正式提交
confidence_committed → 存在明确 ConfidenceAssignment
item_instance → 对应发布快照且呈现配置可复现
```

测试覆盖双击提交、重复与并发 batch、correlation id、worker 租约与崩溃恢复、时钟漂移和系统时间突变、页面刷新、无同步估计时间、离线 IndexedDB、浏览器生命周期、iOS Safari 页面状态、导出期间继续写入、派生血缘、身份边界、purge 失效、课程权限与最小审计。

平台验收保留旧导出 URL、headers、CSRF、权限、提交幂等和快照语义；运行 Django check、全量测试、静态资源收集、两份 JavaScript 语法检查及 PostgreSQL 专项并发测试；在 1440×900 与 390×844 验证数据树、质量页、任务页、治理页和受影响学生路径。

## 11. 实施边界

- 保持 Django 5.2、Django Templates、Bootstrap 5.3、原生 CSS 与原生 JavaScript。
- 不引入 SPA、React、Vue、Tailwind、HTMX、GSAP、npm 或前端构建链。
- 保留现有 URL、表单字段、DOM hook、Fetch 契约，除非同一改动提供兼容层和回归测试。
- `first_opened_at`、`last_opened_at`、草稿非提交、单次有效提交、不同顺序语义、无反馈模式和发布快照不可变等现有科研不变量继续成立。
- 任何触及访问、草稿、提交、计时、反馈、快照、权限、CSRF、幂等或导出的改动，必须先增加自动回归测试。
