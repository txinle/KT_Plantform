# 数据中心全量记录与全过程行为采集审计

> 审计日期：2026-08-29
> 代码分支：`feat/class-teaching-analytics-center`
> 审计代码基线：`d6efe709f3e6c1ae93d8609496f0dff69c69366e`
> 业务数据：工作树 `db.sqlite3`（只读）
> 端到端验证：隔离数据库副本（不修改业务库）
> 关联报告：`docs/audits/data-center-full-export-audit.md`

## 1. 执行结论

### 1.1 直接回答

当前数据中心预览中的数据 **不能称为完整的学生全过程记录**。

当前业务数据只有 4 条行为事件：2 条答案状态变化和 2 条心跳。它没有记录首次进题、离题、题间切换、题目呈现、草稿、时间同步、提交请求、提交成功、attempt 关闭、会话开始/结束等关键过程。因此，教师从当前预览无法还原学生的完整作答时间线。

现行代码具备较广的过程采集能力。隔离多题验证证明，一次“题 1 → 题 2”切换能够形成：

```text
session_started
→ item_entered（题 1，服务器）
→ item_rendered（题 1，客户端）
→ item_left（题 1，客户端，reason=navigation）
→ item_entered（题 2，服务器，from_assignment=题 1）
```

这些记录会进入 `assessment_events`，再正确投影到 `navigation_events`；高级预览、完整 ZIP 和 Manifest 行数一致。

但平台仍 **不能承诺 100% 完整采集所有客户端行为**。提交、评分、会话终态等服务器事实受数据库事务保护；浏览器行为采用持久 outbox 和重试，但采集失败会被吞掉以避免阻断学生作答。浏览器强退、脚本被拦截、设备永久离线且不再返回、IndexedDB 不可用后页面关闭等情形仍可能漏失客户端事件。

### 1.2 三种“完整”必须分开

| 判断层 | 结论 | 证据 |
|---|---|---|
| ZIP 文件内部是否自洽 | **是** | 28 表的预览、CSV 和 Manifest 行数一致；外键分类关系成立。 |
| 当前业务样本是否完整 | **否** | 缺少导航、访问、草稿、校时、提交生命周期等记录，且会话/Attempt 时间与计数异常。 |
| 只凭完整包能否复现实验与全过程 | **否** | 缺参与者、版本配置、会话随机种子、完整题目快照、原始校时样本和足够的派生行为特征。 |

### 1.3 风险等级

| 等级 | 数量 | 含义 |
|---|---:|---|
| P0 | 1 | 若用当前演示数据做科研分析，过程结论不可成立。 |
| P1 | 7 | 会影响样本全集、行为时序或实验可复现性。 |
| P2 | 7 | 表已存在但语义、展示或生产接入不完整。 |

## 2. 审计方法与判据

本次没有把“模型存在”或“CSV 有表头”视为完成。每类记录按以下链条核验：

```text
学生/服务器操作
→ 前端或业务服务触发
→ 接口接收与校验
→ 权威模型落库
→ 分类/派生投影
→ 高级预览
→ 完整 ZIP
→ Manifest
```

采用四类证据：

1. 当前业务库源表与 28 张导出表的只读核对；
2. 前端、服务、模型、导出投影和生产调用路径审查；
3. 隔离多题会话的进题、离题、校时和导航验证；
4. 122 项 Django 目标测试与 16 项 JavaScript collector 测试。

状态定义：

| 状态 | 定义 |
|---|---|
| 已验证 | 存在生产触发、落库链和导出闭环，并有运行或自动化证据。 |
| 部分 | 主链存在，但字段、范围、保证级别或语义不完整。 |
| 条件记录 | 只有触发相应题型、模块或学生操作时才应有数据。 |
| 基础设施 | 有模型/schema/接收端，但没有现行学生端生产者。 |
| 源数据异常 | 表结构可用，但当前记录违反正常业务事实链。 |

## 3. 当前业务数据到底记录了什么

### 3.1 当前源数据摘要

| 对象 | 当前数量 | 结论 |
|---|---:|---|
| 发布参与者快照 | 3 | 发布时 roster 已冻结。 |
| 实验会话 | 1 | 只有 1 名学生形成 Session。 |
| 题目分配 / Attempt | 1 / 1 | 发布版本有 4 道题，但当前会话只保留 1 个 Assignment。 |
| 传统访问事件 | 0 | 没有进题或切题历史。 |
| 草稿保存事件 | 0 | 没有草稿事实。 |
| 权威行为事件 | 4 | 只有 2 次答案状态变化、2 次心跳。 |
| 时间同步样本 | 0 | 不能校正客户端绝对时间。 |
| 答案快照 / 稳定输入观测 | 0 / 0 | 没有文本类或最终状态快照证据。 |
| 提交 / 评分 | 1 / 1 | 有结果事实，但与正常生命周期不闭合。 |
| Confidence | 1 | 状态为 `not_assigned`，不是信心响应。 |
| 仿真 / 条件分配 | 0 / 0 | 当前未启用或未接入。 |

### 3.2 当前 4 条事件

| 顺序 | event_type | 可证明的事实 | 不能证明的事实 |
|---:|---|---|---|
| 1 | `response_state_changed` | 答案状态记录为 A | 何时进入题、是否真实由现行 UI 产生 |
| 2 | `response_state_changed` | 答案从 A 变为 B | 经校时的客户端绝对发生时间 |
| 3 | `activity_heartbeat` | 记录了一次前台/聚焦心跳 | 对应访问区间的开始与结束 |
| 4 | `activity_heartbeat` | 同一客户端单调时间后又有心跳 | 题间切换、提交或会话结束 |

这 4 条事件的 `client_wall_time_utc`、`time_sync_id` 和 `estimated_occurred_at_utc` 全为空。两条心跳具有单调时间，但没有进题/离题边界，不能据此构造完整的活跃区间。

### 3.3 当前源数据异常

| 异常 | 影响 |
|---|---|
| Session `completed_at` 早于 `started_at` | 会话历时为负，任何时长分析失效。 |
| Attempt `submitted_at` 略早于 `first_opened_at` | 首次打开到提交的时间因果顺序被破坏。 |
| `first_to_submit_duration_ms=10000` 与时间戳差值不一致 | 派生时长无法验证。 |
| Attempt `visit_count=0`，但状态为 submitted | 结果事实没有对应访问事实。 |
| Session `submission_count=0`，但存在 SubmissionEvent | 会话计数与权威提交不一致。 |
| 已提交选择题没有 ResponseSnapshot | 当前记录绕过现行提交服务的最终快照保证。 |
| 有效 completed GradeRecord 缺 `graded_at` | 评分时间不可审计。 |

结论：当前数据更接近历史/人工构造的局部演示样本，不是一次由现行业务链完成的学生实验。

## 4. 28 张导出表逐表清查

“当前”列来自业务库完整包；“隔离”列来自增加一个现行四题会话和一次题间切换后的隔离包。

| 表 | 记录内容与触发 | 当前 | 隔离 | 状态 | 审计结论 |
|---|---|---:|---:|---|---|
| `subjects` | 已形成 Session 的学生 | 1 | 2 | 部分 | 不是发布参与者全集；未参与学生依赖 participant snapshots。 |
| `sessions` | 学生开始实验时创建 | 1 | 2 | 源数据异常 | 现行创建链已验证；当前旧会话时间顺序异常。 |
| `assignments` | 新 Session 按版本规则分题 | 1 | 5 | 已验证 | 现行 `selection_mode=all` 为新学生生成 4 题；当前旧会话只有 1 题。 |
| `item_instances` | Assignment 引用的实际呈现实例 | 1 | 4 | 已验证 | 只导出实际分配涉及的实例；默认实例可跨学生复用。 |
| `attempts` | 每个 Assignment 一行作答状态 | 1 | 5 | 部分 | 结构完整；当前历史行的访问、提交、时间和计数不闭合。 |
| `assessment_events` | 全部权威过程事件 | 4 | 9 | 部分 | 服务器事实强保证，客户端事件尽力采集；当前样本严重缺事件。 |
| `response_events` | 作答事件分类视图 | 2 | 2 | 部分 | 分类正确，但只保留服务器接收时间，客户端时序字段被压缩掉。 |
| `navigation_events` | `item_entered` / `item_left` | 0 | 3 | 部分 | 现行切题链已验证；当前无记录；分类表不含客户端发生时间。 |
| `exposure_events` | 反馈、解析、提示、资源暴露 | 0 | 0 | 部分 | 反馈/解析有生产 UI；hint/resource 只有契约，无现行 UI 生产者。 |
| `telemetry_events` | 可见性、焦点、网络、页面、心跳 | 2 | 2 | 条件记录 | collector 已验证；当前只观察到心跳，且仍属客户端尽力采集。 |
| `simulation_events` | 仿真参数、启动、完成、错误 | 0 | 0 | 基础设施 | 有 schema/接收端，没有现行生产页面。 |
| `simulation_snapshots` | 仿真状态元数据与 blob 引用 | 0 | 0 | 基础设施 | 有模型/service，没有生产调用链。 |
| `item_snapshots` | 导出 Assignment 涉及的题目元数据 | 1 | 4 | 部分 | 不是完整发布题目表；且缺题干、选项、正确答案、容差和解释。 |
| `kc_dictionary` | 从已分配题目的主 KC 生成 | 1 | 4 | 部分 | 未按 KC 去重，只含主 KC；不是真正完整 KC 字典。 |
| `item_kc_mapping` | 已分配题目的主/全部 KC | 1 | 4 | 部分 | 对已分配题有效；不覆盖版本中未分配题。 |
| `q_matrix` | 当前与 item-KC mapping 同构 | 1 | 4 | 部分 | 不是题目 × KC 的二元矩阵，名称超出实际语义。 |
| `confidence_assignments` | 每个 Attempt 的信心分配/响应状态 | 1 | 5 | 条件记录 | 当前均为关闭模块的 `not_assigned`；不能解释为学生信心数据。 |
| `attempt_features` | Attempt 的薄派生投影 | 1 | 5 | 部分 | 只有正误、分数、visit_count、打开/提交时间，缺核心过程特征。 |
| `kt_sequences` | Attempt 顺序、Outcome、主 KC | 1 | 5 | 部分 | 是 KT 输入序列表，不是 KT 模型输出；未提交行 Outcome 为空。 |
| `cd_response_matrix` | 每 Attempt 的响应与 KC JSON | 1 | 5 | 部分 | 是长表输入投影，不是传统学生 × 题目响应矩阵。 |
| `calibration_features` | Confidence 与正误误差 | 1 | 5 | 条件记录 | Confidence 关闭时概率/误差为空，只保留 `not_assigned`。 |
| `response_snapshots` | 文本类稳定答案和最终响应状态 | 0 | 0 | 条件记录 | 现行代码会在文本保存或选择题提交时生成；当前样本没有。 |
| `response_input_observations` | 文本编辑 hash/长度/操作/输入法 | 0 | 0 | 条件记录 | 只在文本类输入时产生；不记录逐键文本，符合隐私边界。 |
| `submissions` | 服务器接受的正式提交事实 | 1 | 1 | 已验证/当前异常 | 事务链已验证；当前旧提交缺配套会话计数与生命周期。 |
| `grade_records` | 版本化评分事实 | 1 | 1 | 已验证/当前异常 | 模型链已验证；当前有效评分缺 `graded_at`。 |
| `condition_assignments` | 实验条件随机分配 | 0 | 0 | 基础设施 | 有模型和导出逻辑，未发现现行业务创建路径。 |
| `class_snapshots` | 发布时冻结班级身份 | 1 | 1 | 已验证 | 当前源表与导出一致。 |
| `participant_snapshots` | 发布版本应参与者全集 | 1 | 2 | 导出缺陷 | 源表实际为 3；导出错误地按已有 Session 学生过滤，漏掉未参与学生。 |

## 5. 全部行为事件清查

平台注册了 41 类事件。注册 schema 只证明“允许接收”，不证明“现行界面一定产生”。

| 行为组 | 事件类型 | 生产者 | 保证级别 | 当前观测 | 结论 |
|---|---|---|---|---:|---|
| 会话生命周期 | `session_started`, `session_completed`, `session_expired`, `session_invalidated` | 服务器业务服务 | 事务强保证 | 0 | 现行链已验证；当前旧数据缺失。 |
| 题目进入 | `item_entered` | 服务器 question view | 事务强保证 | 0 | 隔离验证成功。 |
| 呈现与离题 | `item_rendered`, `item_left` | 浏览器 collector | 尽力采集 | 0 | 隔离验证成功，但不能承诺强退时不丢。 |
| 作答开始与编辑 | `response_input_started`, `text_edit_observed`, `response_state_changed` | 浏览器 + 草稿接口 | 尽力采集/草稿强事实 | 2 | 当前仅有两个状态变化；文本编辑未观察。 |
| 提交请求 | `response_commit_requested` | 浏览器 | 尽力采集 | 0 | 可能因客户端失败缺失，不能当成提交真值。 |
| 提交结果 | `response_committed`, `response_commit_rejected`, `attempt_closed` | 服务器 | 事务强保证 | 0 | 当前旧提交没有这些现行权威事件。 |
| 反馈/解析 | `feedback_shown`, `explanation_shown` | 当前提交反馈 UI | 尽力采集 | 0 | 已接入，但当前无观测。 |
| 提示/资源 | `hint_opened`, `hint_closed`, `resource_opened`, `resource_closed` | 暂无现行 UI | 基础设施 | 0 | 不能宣称已采集。 |
| 页面遥测 | `document_visible`, `document_hidden`, `window_focused`, `window_blurred`, `pageshow`, `pagehide`, `network_online`, `network_offline`, `activity_heartbeat` | 浏览器 collector | 尽力采集 | 2 | 当前仅心跳。 |
| Confidence | `confidence_prompt_shown`, `confidence_committed`, `confidence_skipped` | 服务器 confidence endpoints | 事务强保证 | 0 | 模块关闭，0 行合理。 |
| 仿真 | `simulation_parameter_changed`, `simulation_run_started`, `simulation_run_completed`, `simulation_error_returned` | 暂无现行仿真 UI | 基础设施 | 0 | schema/model 不等于实际功能。 |
| 历史投影 | `legacy_visit_observed`, `legacy_draft_observed`, `legacy_submission_observed`, `legacy_session_start_observed`, `legacy_session_end_observed` | 显式迁移命令 | 条件迁移 | 0 | 只在执行 legacy projection 后存在。 |

## 6. “切换题目与每次切换时间”的准确答案

### 6.1 能否记录切题

能，但要区分当前数据与现行能力：

- 当前截图对应的数据：不能，因为 `navigation_events=0`；
- 现行多题流程：能，隔离验证得到 2 条进入和 1 条离题；
- 完整保证：不能，`item_left` 是客户端尽力上传事件。

### 6.2 保存了哪些时间

| 字段 | 语义 | 适用范围 | 能否当作真实发生时间 |
|---|---|---|---|
| `client_wall_time_utc` | 客户端壁钟 | 客户端事件 | 否，可能被修改或漂移。 |
| `client_monotonic_ms` | 同一浏览器实例内单调时间 | 客户端事件 | 可比较同一 instance 内顺序和间隔。 |
| `server_received_at` | 服务器收到事件时间 | 全部事件 | 不能替代学生发生时间；离线补传会延迟。 |
| `server_committed_at` | 服务器邻近持久化时间 | 全部事件 | 可证明保存顺序，不是数据库提交边界。 |
| `estimated_occurred_at_utc` | 基于有效校时估计的发生时间 | 有有效 sync 的客户端事件 | 是估计值，不是精确真值。 |

一次完整切题应使用：

```text
item_left.client_monotonic_ms / estimated_occurred_at_utc
→ item_entered.server_received_at
→ item_rendered.client_monotonic_ms / estimated_occurred_at_utc
```

其中 `item_entered` 是服务器事件，没有客户端时间；真正“新题已经在浏览器可交互”的客户端边界应由 `item_rendered` 表达。

### 6.3 为什么分类预览看不到这些时间

`navigation_events` 与 `response_events` 使用 9 列紧凑视图，只保留：

```text
student_number, student_name, question_code,
event_id, event_type, session_id, attempt_id,
server_received_at, payload
```

客户端时序与质量字段只存在于 `assessment_events` 的 31 列权威表。也就是说，即使切题已被完整采集，教师在 `navigation_events` 分类预览仍只能看到服务器接收时间，不能直接看到客户端发生时间。这是展示/导出投影缺口，不是浏览器水平滚动的问题。

## 7. 完整包仍缺少的必要记录

### 7.1 建议新增的导出表

| 建议表 | 来源模型 | 必要性 | 当前替代信息为何不足 |
|---|---|---|---|
| `experiment_versions` | `ExperimentVersion` | P1 | Manifest 只有版本号，没有完整冻结配置。 |
| `client_time_syncs` | `ClientTimeSync` | P1 | 事件行只有估计结果，不能复核同步的原始四时间点与质量判定。 |
| `conditions` | `Condition` | P2 | 只有发生 assignment 时才带 key/label，无法导出完整条件设计。 |
| `derivation_definitions` | 派生配置/代码参数 | P1 | `derivation_version=v1` 不足以复现 idle threshold、窗口和算法参数。 |

### 7.2 现有表必须补充的字段

| 表 | 必补字段/结构 | 原因 |
|---|---|---|
| `sessions` | sampling seed、ordering seed、algorithm version、assignment unit、deadline | 重放个人分题与顺序。 |
| `item_snapshots` | 题干、选项、标准答案、accepted answers、容差、解释、难度、来源版本 | 复核刺激材料与评分。 |
| 5 张分类事件表 | 客户端 instance/sequence、wall/monotonic、sync、estimated time、quality flags、capture mode | 直接分析分类事件时不能丢失时序与质量。 |
| `participant_snapshots` | 不按 Session 过滤 | 保留未参与者与完成率分母。 |
| `kc_dictionary` | KC 唯一标识、去重、主/次 KC 全集 | 形成真正字典。 |
| `q_matrix` | 明确的题目 × KC 0/1 矩阵或改名为 mapping | 避免语义误导。 |
| `attempt_features` | 修改次数、首次/最终状态、不同状态数、DecisionTime、RevisionTime、ActiveResponseTime、回访、质量标记 | 承接全过程采集的实际科研价值。 |

## 8. 主要问题与优先级

### P0：当前演示数据不可作为科研样本

当前会话、Attempt、提交和事件链不闭合。继续用它展示“全过程数据已完整记录”会形成错误结论。应删除或隔离该演示轨迹，使用现行业务链重新生成明确标记为 synthetic 的完整四题样本。

### P1-1：发布参与者导出漏人

`participant_snapshots` 当前按已形成 Session 的学生过滤。未参与学生被排除后，完整包无法重建完成率分母。必须按版本/班级冻结范围导出全部 participant snapshots，再由 Sessions 表区分是否参与。

### P1-2：分类视图丢失客户端时序

分类表不应只保留服务器接收时间。至少要保留权威事件表的时序、质量和捕获来源字段，否则教师和研究者会误把接收时间当作行为发生时间。

### P1-3：完整包缺实验版本与随机化上下文

缺完整 Version configuration、session seeds 和算法版本后，无法从包内复现分题、排序、时间限制、反馈与 confidence 设计。

### P1-4：题目快照不是完整快照

当前 `item_snapshots` 只导出分析标签，不含刺激与评分所需字段。若称“完整研究包”，必须保留不可变题目材料和评分规则，或明确提供受控的独立 item package。

### P1-5：客户端行为是尽力采集，产品文案不能写“完整无丢失”

应记录采集覆盖率、outbox 待上传/失败状态、末次确认序号和缺口；分析时按 Session 给出完整、部分、不可评估，而不是默认每个无事件都表示学生没有该行为。

### P1-6：原始校时样本未导出

没有 `ClientTimeSync` 全记录，`estimated_occurred_at_utc` 的估计无法在包外复核。应导出 sync 样本并与事件的 `time_sync_id` 保持外键关系。

### P1-7：派生层尚未消费全过程数据

虽然采集了答案状态和心跳，`attempt_features` 尚未生成答案修改、决策、修订、活跃时间等核心特征。当前“派生研究数据”更多是薄投影，不是完整可复现特征层。

### P2 问题

1. hint/resource 只有契约，没有 UI 生产者；
2. simulation 只有基础设施，没有端到端模块；
3. condition assignment 没有生产创建链；
4. `kc_dictionary` 未去重且遗漏次 KC 独立条目；
5. `q_matrix` 名称与实际结构不符；
6. Confidence 关闭时表有 `not_assigned` 行但高级工具隐藏该扩展，发现性不一致；
7. 当前有效评分缺 `graded_at`。

## 9. 建议的修复与验收顺序

### 第一批：先保证研究事实可解释

1. 修复 participant snapshots 全量导出；
2. 用现行业务链重建演示作答数据，并加 fixture provenance；
3. 为分类事件表补齐客户端时序、sync、quality 与 capture fields；
4. 新增 `client_time_syncs` 与 `experiment_versions`；
5. 在数据质量页增加“客户端事件缺口/末次 ack/未同步/离线上传”覆盖指标。

验收标准：

- 3 名发布参与者、1 名完成时，participant snapshots=3、subjects=1、sessions=1；
- 一次题 1→题 2 切换，navigation events 至少包含 enter1/left1/enter2，并能关联 rendered2；
- `left1` 与 `rendered2` 均有同一 client instance 内可比较的 sequence/monotonic time；
- 分类预览、权威表、ZIP 和 Manifest 对同一事件给出相同时间与质量字段；
- 模拟永久离线后明确标记 Session 过程数据为 partial，不把缺事件解释成“无行为”。

### 第二批：补齐实验设计可复现性

1. 导出完整版本配置、会话随机种子和算法版本；
2. 导出完整不可变题目材料与评分规则；
3. 修正 KC 字典和 Q 矩阵结构；
4. 补 conditions 定义及其分配生产链。

验收标准：仅使用 ZIP，可以重建该版本的候选题集、个人分题、题序、呈现、计时、反馈、评分和实验条件。

### 第三批：让过程数据真正进入分析层

1. 版本化生成首次答案、最终答案、修改次数、不同状态数；
2. 生成 DecisionTime、RevisionTime、ActiveResponseTime 与回访特征；
3. 每个派生值携带 derivation version、参数 hash、输入覆盖和不可评估原因；
4. hint/resource/simulation 仅在生产端真正接入后开放相应数据表和 UI。

## 10. 验证记录

| 验证项 | 结果 |
|---|---|
| 当前业务库只读源表核对 | 完成 |
| 隔离四题 Session 创建 | 4 个 Assignment，完成 |
| 隔离题间切换链 | enter1 / rendered1 / left1 / enter2，完成 |
| 有效客户端时间同步 | 完成；离题事件得到 sync 和 estimated time |
| 高级预览 `navigation_events` | 3 行，完成 |
| 28 表预览 vs ZIP CSV | 28/28 行数一致 |
| Manifest vs ZIP CSV | 28/28 行数一致 |
| Django 目标测试 | 122/122 通过 |
| JavaScript collector 测试 | 16/16 通过 |

## 11. 最终审计意见

平台已经具备一套可用的事件采集骨架，且现行多题导航能够落库并进入数据中心。问题不在于“所有表都是空壳”，而在于当前演示数据没有走完整链路、客户端采集不能被表述为强保证、分类视图压缩掉关键时间字段，以及完整包仍缺实验设计和校时复现所需的上下文。

在 P0/P1 问题修复前，最严谨的产品表述应是：

> 平台记录服务器权威作答事实，并尽力采集客户端过程事件；每个导出包提供事件覆盖与质量信息。无事件不自动解释为无行为，过程不完整的会话不得进入需要完整时序的分析。

不应表述为：

> 平台已经完整、无遗漏地记录每名学生的全部作答过程。
