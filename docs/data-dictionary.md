# 核心数据字典

本字典区分三类数据：平台业务状态、不可变原始观测和可重新生成的派生数据。除非字段定义明确说明，否则不得把行为日志直接命名为“犹豫”“粗心”“误概念”或“不确定性”；这些均属于带版本的派生解释，而不是原始事实。

- `Attempt.first_opened_at`: 学生第一次进入题目的服务器时间。
- `Attempt.last_opened_at`: 正式提交前最后一次进入题目的服务器时间。
- `Attempt.last_visit_to_submission_ms`: `submitted_at - last_opened_at`。
- `Attempt.first_open_sequence_index`: 题目首次打开顺序。
- `Attempt.submission_sequence_index`: 题目正式提交顺序。
- `StudentQuestionAssignment.assigned_sequence_index`: 系统分配后的题目顺序。
- `QuestionVisitEvent`: 每次题号导航或恢复进入题目的明细事件。
- `SubmissionEvent.committed_at`: 数据库正式提交完成时间，作为正式提交时间。

## 课程题目标识、选题与会话

- `CourseQuestionIdentity`：课程题目的永久身份。课程内 `course_sequence` 从 1 起严格递增、唯一且不复用；`stable_code` 形如 `C000001-Q000001`。同课程内更换题库不改变身份，跨课程移动被拒绝；删除题目后身份保留、题目关联置空，后续题目不会占用旧序号。
- `QuestionSnapshot.course_sequence` 与 `stable_code`：发布时冻结的课程级永久编号，用于跨题库追溯。
- `QuestionSnapshot.source_bank_id`、`source_bank_name` 与 `bank_sequence`：发布时来源题库及其本地序号，只解释题目在该题库中的位置。
- `QuestionSnapshot.code`：题目作者或导入时的题目标识，保留给显示和兼容旧导出，不替代课程永久编号。
- `QuestionSnapshot`：还会冻结题干、题型、选项、计分答案、容差、解析、KC 和难度；课程、题库、题目或 KC 后续修改不回写已发布快照。

题型枚举为 `single_choice`（单选）、`multiple_choice`（多选）、`numeric`（数值）、`short_answer`（简答）、`fill_blank`（填空）和 `true_false`（判断）。选择题只使用 A–F 选项；数值答案必须为有限数值；填空和简答可有其他可接受答案；判断题标准答案为布尔值。

- `Experiment.selection_mode`：`all` 为全量候选池，`quota` 为按六种题型配额抽题。
- `ExperimentQuestionQuota`：每个实验、每种题型一条非负配额记录。配额模式总数必须大于 0，发布前每种题型可用题数必须不少于所需配额。
- `ExperimentVersion.configuration`：发布时保存选题方式、配额、候选库存、候选总数、`sampling_salt` 和 `sampling_algorithm_version`。发布冻结的是题库全部可用题形成的候选池，不会在学生开始后加入新题。
- `ExperimentSession.sampling_seed`、`ordering_seed` 和 `sampling_algorithm_version`：会话首次创建时分别保存抽题、排序和算法版本。前两者由发布版本的盐、学生 ID 与不同用途独立派生；同一版本和学生可复现，重复进入不会重新抽题或重新排序。没有盐的旧版本保留 `legacy-v0` 排序流。
- `StudentQuestionAssignment.assigned_sequence_index`：该学生实际获分配题目的稳定顺序。学生题号导航、会话详情和 Word 报告均以此顺序为准，不以 `course_sequence` 替代。

配额抽题在各题型候选中无放回抽取。固定顺序把该学生抽到的集合按发布时题库内序号排列；随机和分层随机只改变该集合的展示顺序，后者以主 KC 与教师难度三档分组交错。

## 课程班级与学生账号

- `User.student_number`：平台级唯一学号；同一账号可以加入多门课程。
- `CourseEnrollment`：学生在单门课程中的唯一选课关系，`class_group` 表示该课程下的班级归属。
- 手动添加和 Excel 导入复用同学号、同姓名的既有账号；新账号密码规则为 `S + 原始学号`。
- 从其他课程班级复制时只复制有效 `CourseEnrollment`；目标课程已有选课的学生被跳过，不复制账号、不改密码、不自动转班。
- 实验分配时保存 `ExperimentStudent.class_snapshot_name`；班级后续改名不改变该快照。

## 稳定研究对象标识

- `CourseSubject.public_uuid`：同一学生在同一课程中的稳定随机 UUID。不同实验复用该标识，因此可用于课程内纵向分析；同一学生在不同课程中的 UUID 不同。
- `ResearchStudy.public_uuid`：显式研究项目的公开 UUID。研究项目必须由有权限的负责人创建，不能从一次导出临时推断。
- `ResearchStudyParticipant.public_uuid`：同一学生在同一显式研究项目中的稳定随机 UUID；跨研究项目不同。
- `ResearchStudyCourse`：研究项目与课程的显式纳入关系，不表示自动授权访问课程身份数据。

这些标识只构成**去标识化（pseudonymized）**数据，不构成匿名数据。稳定纵向轨迹、时间戳和课程上下文本身仍可能产生再识别风险。用户主键、学号、姓名与上述 UUID 的关系只能保存在身份安全域，不能与去标识化研究包同包导出。

## 呈现实例与实验采集配置

- `ItemInstance.id`（导出列名 `item_instance_id`）：学生实际呈现题目版本的 UUID。它引用不可变 `QuestionSnapshot`，并冻结 `presentation_variant`、`option_order`、`parameter_values`、`random_seed` 与 `presentation_hash`。
- `presentation_hash`：对呈现变体、选项顺序和参数值的规范 JSON 计算 SHA-256；相同快照和相同呈现状态复用同一默认实例。
- `Experiment.confidence_mode`：`off`、`all` 或 `sampled`。
- `Experiment.confidence_sampling_unit`：`student`、`session` 或 `attempt`，只解释随机分配单位。
- `Experiment.confidence_probability`：抽样模式下的分配概率，范围 `[0, 1]`；不是学生信心值。
- `confidence_randomization_seed`、`confidence_assignment_version`、`confidence_scale_version`、`confidence_prompt_version`：分别控制分配复现、分配算法、量表和题干版本。
- `heartbeat_interval_seconds`：活动心跳间隔，允许 15–120 秒；默认 30 秒。

发布时上述值被复制到 `ExperimentVersion.configuration`。发布后的版本配置不随实验草稿修改而变化。

## 不可变过程事件

### AssessmentEvent 主键与关联

- `event_id`：随机 UUID 主键。
- `event_domain`：`assessment`、`telemetry` 或 `system`；教育过程事件不得与运行监控事件混作同一语义域。
- `event_type` + `event_version`：事件名称及该事件独立 payload schema 版本。每种事件版本对应仓库内一份 Draft 2020-12 JSON Schema，禁止未声明字段。
- `envelope_version`：公共事件信封版本，与 payload 的 `event_version` 分开演进。
- `actor_source`：`client`、`server` 或 `system`，表示事件观测来源，不等同于学生身份。
- `session_id`：必填并使用 `PROTECT`。`assignment_id`、`attempt_id`、`question_snapshot_id`、`item_instance_id` 可空，以支持会话级事件；非空时必须与会话和分配一致。
- `correlation_id`：连接客户端请求、服务端成功或拒绝结果的 UUID；不能用时间邻近替代请求关联。

### 顺序与幂等

- `client_instance_id` + `client_sequence_no`：客户端实例内的原始行为顺序和全局幂等键，必须同时为空或同时非空。浏览器刷新或重新加载可能产生新的 `client_instance_id`。
- `server_ingest_sequence_no`：会话内服务器持久化接收顺序，从 1 递增并唯一。

必须遵守：

```text
server_ingest_sequence_no != 学生真实行为顺序
```

网络缓存、重试和离线补传都可能使服务器持久化顺序不同于客户端发生顺序。重建行为过程时优先使用 `(client_instance_id, client_sequence_no)`；跨客户端实例只能结合页面生命周期、服务器时间和质量标记做带假设的重建。相同客户端幂等键且内容完全一致时返回既有事件；内容冲突时拒绝写入，不覆盖原事件。

### 时间语义

- `client_wall_time_utc`：客户端壁钟时间，可能错误或被修改。
- `client_monotonic_ms`：客户端单调时钟，只在同一 `client_instance_id` 与对应 `client_time_origin` 内有意义，不能跨刷新直接比较。
- `server_received_at`：事件进入唯一追加服务时立即记录的服务器时间；不是客户端行为发生时间。
- `server_committed_at`：通过校验、获得会话锁后，紧邻数据库插入记录的服务器持久化时间。它能证明已保存记录的持久化先后，但不是数据库事务提交边界的精确测量。
- `time_sync_id`：可空，指向一次客户端时间同步样本。
- `estimated_clock_offset_ms`、`estimated_rtt_ms`、`estimated_occurred_at_utc`：仅当同步样本状态为 `valid` 且属于同一 `client_instance_id` 时生成；否则必须为空。估计发生时间是测量估计，不是精确真值。

### 采集质量与不可变性

- `capture_mode`：`native` 表示新采集器原生观测；`legacy_projection` 表示历史数据投影。历史事件使用独立 `legacy_*_observed` 类型，不伪装成原生事件。
- `quality_flags`：允许 `clock_unsynchronized`、`offline_buffered`、`mid_session_capture`、`late_arrival`、`payload_recovered`；未知标记被拒绝。多个标记可同时存在。
- `payload`：只保存对应 `(event_type, event_version)` schema 允许的最小状态。文本正文、完整仿真状态、鼠标轨迹和按键内容不得借由通用 JSON 绕过治理边界。
- `app_version`：产生该事件的客户端或服务版本。

`AssessmentEvent` 是应用层 append-only：普通模型更新、批量更新、单条删除和批量删除均被拒绝，Django 管理界面只读。依法删除、研究退出、保留期到期或错误采集必须走后续受控 purge 流程并保留不含原内容的审计记录；“不可变”不等于“永不允许依法删除”。

所有事件只能由 `append_assessment_event` 追加。服务会在获得会话行锁后再次检查依赖当前事件数的科研不变量，以避免并发请求同时通过锁前校验。`response_committed` 和 `attempt_closed(close_reason=submitted)` 只能由服务器产生；客户端只能记录 `response_commit_requested`。数据库对每个 `attempt_id` 最多一条 `response_committed` 建立条件唯一约束，因此即使绕过应用层或发生并发竞态，也不能生成两个正式提交事实。

正式提交采用同一数据库事务写入 `Attempt` 最终状态、`SubmissionEvent`、唯一 `response_committed` 和 `attempt_closed`。`response_committed.submission_token`、得分与正确性必须与权威 `SubmissionEvent` 一致；任一事件写入失败时业务提交整体回滚。该约束保证原始事件与业务事实一致，但不把客户端的 `response_commit_requested` 误当成成功提交。

## 时间同步与状态快照

- `ClientTimeSync.time_sync_id`：时间同步样本 UUID；保存客户端实例、壁钟、单调时钟、时间起点、服务器接收/响应时间、估计偏移、RTT 与 `sync_quality`。偏移和 RTT 是估计量。
- `ResponseSnapshot.snapshot_id`：长文本响应状态 UUID。按 `attempt_id + content_hash` 去重，保存正文、长度、SHA-256、schema 版本与创建时间。事件 payload 只引用快照 ID、长度和哈希。
- `SimulationSnapshot.snapshot_id`：仿真状态 UUID。保存小型结构化元数据、外部 blob 键、内容 SHA-256、字节数、仿真版本和 schema 版本；完整电路、波形或文件不进入事件 JSON。

快照与其引用事件都是原始观测层。统计用的答案修改次数、首次选择、首次选择时间、决策时间、修改时间、不同答案数、回访次数和累计活跃时长均从事件流派生，不作为并行的“原始事实字段”重复采集。

## Confidence 分配与响应投影

`ConfidenceAssignment` 每个 Attempt 最多一条，用于明确区分“未被分配”和“被分配但没有事件”：

- `condition`：`not_assigned`、`control` 或 `prompt`。
- `assignment_unit` 与 `assignment_version`：记录学生级、会话级或作答级分配及算法版本。
- `response_status`：`not_assigned`、`assigned`、`prompt_shown`、`committed`、`skipped` 或 `upload_failed`。
- `probability`：学生提交的 0–100 整数概率，只在 `committed` 时非空；当前 v1 事件 schema 约束为 10 的倍数。
- `prompt_shown_at` 与 `committed_at`：分别记录提示实际显示和信心正式提交时间。

没有 `confidence_committed` 事件不能直接解释为低信心或拒答；必须联合分配条件、显示状态、跳过状态和上传状态分析。

## 原始数据与派生数据边界

原始层只承诺 Outcome、Process、Exposure、Self-report 与 Simulation 的可观测事实。以下变量必须在派生层生成并携带 `derivation_version`、源数据冻结点、源 schema 版本、代码版本、参数配置哈希和生成时间：

- 首次选择、答案修改次数、不同答案选择数、最后修改时间；
- DecisionTime、RevisionTime、ActiveResponseTime、累计活跃访问时间；
- 跳过后返回、提交前仍在修改、Hint/资源使用汇总；
- KT/CD 输入矩阵、行为特征、任何“不确定性”“犹豫”“粗心”或“误概念”指标。

派生字段的名称相同不代表定义相同。例如 idle threshold 从 60 秒改为 30 秒时，必须提升派生版本，不能覆盖历史结果。

## 课程 KC 与章节视图

- `Concept.course`：课程 KC 的所属课程；同一课程按 `normalized_name` 唯一。
- `QuestionConcept`：题目与课程 KC 的映射；每题最多一个 `is_primary=True` 的主 KC，并可关联多个次 KC。
- “课程 KC 总库”统计课程内全部题库和题目关联；“本题库章节 KC”只统计当前题库引用的同一批课程 KC，不是独立数据实体。
- `QuestionSnapshot.primary_kc` 与 `QuestionSnapshot.all_kcs` 在实验发布时复制 KC 名称；后续 KC 重命名不回写历史快照。

## 实名可选字段 Excel

工作表 `KT数据` 的观测单位为“学生 × 题目”，每个 `Attempt` 一行。界面默认选择全部字段，服务端按下列固定顺序输出所选列：

- 身份：`student_number`（学号）、`student_name`（姓名）、`class_name`（会话发布时记录的班级）。
- 课程快照：`course_name`、`course_code`、`course_term`。
- 实验与会话：`experiment_id`、`experiment_version`、`session_id`、`session_status`。
- 课程题目标识：`course_sequence`、`stable_code`、`source_bank_id`、`source_bank_name`、`bank_sequence`。
- 题目与知识点：`question_code`、`question_type`、`primary_kc`、`all_kcs`、`teacher_difficulty`、`empirical_difficulty`。
- 顺序与行为：`assigned_sequence_index`、`first_open_sequence_index`、`submission_sequence_index`、`visit_count`。
- 时间：`first_opened_at`、`last_opened_at`、`submitted_at`、`first_to_submit_duration_ms`、`last_visit_to_submission_ms`。
- 作答结果：`final_answer`、`is_correct`、`score`、`attempt_status`。

行先按学号排序；同一学生内优先按非空 `course_sequence` 升序，再按 `assigned_sequence_index` 和记录主键排序。因此 Excel 的题目主排序是课程永久序号，而学生答题页采用个人获分配顺序。学号、姓名和答案以 Excel 文本单元格写入，保留前导零且不执行以 `=`、`+`、`-` 或 `@` 开头的公式文本。该工作簿含直接身份信息，不属于匿名数据。

## 会话详情与 Word

会话详情与 Word 报告按学生实际的 `assigned_sequence_index` 展示学生身份、发布时班级、实验版本、会话状态、实际题数，以及每题的稳定课程代码、题型、题干、选项、学生答案、标准答案和作答状态。草稿明确标为未正式提交。

Word 报告禁止包含 KC、教师或经验难度、任何用时字段、访问/导航事件和解析；它是单个学生的作答核对材料，不是过程数据或心理测量报告。配额模式下不同学生可能收到不同的题目集合，不得把跨学生的原始得分总和或百分比直接解释为可比较的能力差异。

## 兼容导出

旧版界面所称“匿名 KT”和“匿名导航”仅是兼容名称，方法学上应解释为**实验内去标识化 CSV**。URL 和 headers 暂时保持不变：`/analytics/experiments/{experiment_id}/kt.csv` 与 `/analytics/experiments/{experiment_id}/navigation.csv` 继续以 `anonymous_student_id` 标识学生，不新增学号、姓名、课程永久编号或其他实名列。身份对照 CSV 暂时仍在 `/analytics/experiments/{experiment_id}/identity.csv` 单独提供既有映射 headers；它属于受限身份数据，不得与研究 CSV 合并或同包共享。后续数据中心会把身份映射迁入独立权限与审计域。兼容 CSV 使用 UTF-8 BOM。
