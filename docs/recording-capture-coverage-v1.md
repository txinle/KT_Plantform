# 学生全过程行为采集与 RecordingCoverage v1 数据契约

## 1. 可声称与不可声称的能力

产品固定表述：

> 平台事务性记录服务器权威生命周期、提交和评分事实，并通过持久 outbox、ACK、checkpoint 和时间同步采集客户端过程事件；每个 Session/Attempt 同时提供记录完整性及分析资格，缺失事件不会被解释为未发生行为。

不得使用“全过程无遗漏”“真实学习时间”“犹豫”“注意力不集中”“能力下降”或“掌握不足”等表述。客户端进程被终止、脚本被阻止、设备永久离线或 IndexedDB 不可用时仍可能缺失过程事件。

## 2. 版本边界

| 工件 | 当前版本 | 不可变规则 |
|---|---|---|
| 事件信封 | v2 | 原始 `AssessmentEvent` 追加后不更新 |
| 采集契约 | `capture-v1` | Session 创建时绑定 ID 和 SHA-256 |
| 完整性算法 | `coverage-v1` | 已完成 Run、Session 结果和 Attempt 结果只追加 |
| 稳定输入 | 800 ms | 参数进入 CaptureContract hash |
| 转换等待 | 1500 ms | ACK 超时不阻断合法导航或正式提交 |
| 终态宽限 | 300 s | 终态后才由 finalizer 生成首个 finalized Run |
| 校时间隔/有效窗 | 15/20 min | 后验估计不回写原始事件 |
| ActiveTime | 30/60/120 s | 教师默认 60 s，heartbeat 不重置 idle |

旧 Session 不自动绑定新契约，不补造历史 `item_left`、答案变化或时间，统一评估为 `legacy_unknown`。

## 3. 原始事实链

### 3.1 权威服务器事实

`session_started/completed/expired/invalidated`、`item_entered`、`response_committed/rejected` 与 `attempt_closed` 由服务器事务写入。Session 的 `student`、`capture_contract` 和条件关系冻结；终态必须满足：

```text
in_progress                  terminal_at = NULL
completed/expired/invalidated terminal_at != NULL
```

Submission、最终 ResponseSnapshot、有效 GradeRecord 或对应服务器事件互相矛盾时，Coverage 为 `invalid`。客户端状态链缺口只影响过程资格，不污染权威 Outcome。

### 3.2 客户端过程事实

每个事件先写入 IndexedDB，再开始网络请求。每个浏览器页面实例使用新的 `client_instance_id`，并维护独立、严格递增的 `client_sequence_no`。

| 行为 | 原始事件/工件 | 关键事实 |
|---|---|---|
| 页面开始 | `pageshow`、初始 visible/focus、resume checkpoint | 区分状态未变化与 collector 未启动 |
| 题目呈现 | `item_rendered.v2` | `presentation_state=interactive` |
| 切题/返回 | `item_left.v2 → item_entered.v2 → item_rendered.v2` | left 包含 `to_assignment_id`，enter 包含 from |
| 选择题修改 | `response_state_changed.v2` | 完整 before/after；首次 before 为 `null` |
| 数值/填空 | 同上 | 保留词法字符串，不提前数值化 |
| 长文本 | `text_edit_observed.v2` + ResponseSnapshot | 仅操作、长度、hash、批次时间和 IME；无逐键正文 |
| 页面状态 | visibility/focus/network/heartbeat | heartbeat 只证明采集在线 |
| 提交 | requested checkpoint → Submission → committed → closed | 过程上传失败不回滚正式提交 |

未发生真实输入时，不把多选 `[]` 或数值/填空 `""` 伪造为首答；学生在已有答案后删除到空状态仍保留为一次状态变化。

## 4. ACK、Checkpoint 与缺口

IndexedDB v2 包含 `events`、`checkpoints`、`meta` 和 `dead_letters`，升级不清空 v1 未上传事件。

```text
highest_emitted_sequence_no
highest_acked_sequence_no
highest_contiguous_acked_sequence_no
pending_event_count
failed_event_count
missing_local_sequence_ranges
```

最大 ACK 不能替代连续 ACK。服务端收到 `1,2,3,4,6` 时必须输出 `[[5,5]]`。网络错误、超时、408、429 和 5xx 重试；明确的其他 4xx 进入 dead letter。heartbeat、navigation、commit、pagehide、resume、session_end 和连续 ACK 前进均可产生不可变 checkpoint。

## 5. 时间语义

| 等级 | 可用于什么 | 不可用于什么 |
|---|---|---|
| `exact_monotonic` | 同一 instance/origin 内排序与间隔 | 跨实例直接相减 |
| `synchronized_estimate` | 有效校时后的绝对时间估计 | 宣称精确客户端时刻 |
| `client_wall_unsynchronized` | 展示客户端声明的壁钟 | 精确间隔或跨设备比较 |
| `server_only` | 接收/提交顺序兜底 | 替代客户端发生时间 |
| `unknown` | 明示不可评估 | 填零或推断未发生 |

后验校时使用同 Session、同 client instance、同 time origin、20 分钟有效窗内最近的有效 sync：

```text
derived time = sync client wall time
             + estimated clock offset
             + (event monotonic - sync monotonic)
```

结果只进入 Coverage/导出派生字段 `derived_occurred_at_utc`，不回写原始事件。

## 6. RecordingCoverage 输出

总体状态固定为：`complete`、`partial`、`insufficient`、`invalid`、`not_applicable`、`legacy_unknown`。`pending_finalization` 只用于页面等待态，不写成第七种 finalized 状态。

每项分析资格均输出：

```text
status
usable
missing_reasons
time_quality
coverage_run_id
```

当前实现逐 Attempt 输出 Outcome、First response、Final response、Revision、DecisionTime、RevisionTime、Navigation、Return behavior、ActiveTime、Text editing 和 Response replay 资格。不可计算的派生值保持 `null`，并携带 derivation version、parameter hash、run ID、输入状态、时间质量和缺失原因。

### 6.1 教师显示与研究导出边界

教师过程页将完整性与时间质量转换为中文教学结论，例如“过程记录不完整”“步骤间隔可可靠比较”。原始 Coverage 状态码、缺失原因码、CoverageRun UUID 和事件时间来源枚举仅在数据中心研究导出中保留，不作为教师页面的显示内容。

教师页面中的“服务器接收时间”表示平台收到记录的时间，不表示学生实际操作时刻；缺失记录不解释为学生未操作。

## 7. 数据中心导出

complete/process 包含 33 张表，新增：

```text
01_design_metadata/capture_contracts.csv
03_raw_events/client_time_syncs.csv
03_raw_events/client_capture_checkpoints.csv
07_quality/recording_coverage_sessions.csv
07_quality/recording_coverage_attempts.csv
```

`assessment_events` 保存完整原始信封；response/navigation/exposure/telemetry 分类表复用同一事件行构建器，并保留 sequence、monotonic、sync、原始/派生时间、actor、capture mode、quality 和 CoverageRun。科研 `ExportSnapshot` 在创建时绑定一个不可变的 cohort CoverageRun；预览、单表、ZIP 和 Manifest 均读取该 Run，不在下载期间漂移到更新结果。Manifest 记录状态、sequence gap、tail gap、dead letter 与时间质量计数。

## 8. 运行与发布

```powershell
python manage.py migrate
python manage.py finalize_recording_coverage
```

finalizer 只处理终态已超过 300 秒的 Session。开启班级中心和学生“我的学习”还需要：

```text
CLASS_CENTER_ENABLED=1
STUDENT_LEARNING_ANALYTICS_ENABLED=1
```

直接运行 `manage.py runserver` 而未设置变量时，旧 roster 页仍作为回滚界面保留；项目启动脚本默认显式设置两个变量。

## 9. 研究使用限制

- `complete` 只表示满足 `capture-v1/coverage-v1`，不表示现实行为绝对无遗漏。
- `partial`/`insufficient` 的缺失事件不能编码为行为未发生。
- `legacy_unknown` 不得与新契约 Session 混合计算过程发生率。
- ActiveTime 三阈值必须作为敏感性组报告，不称为真实学习时间。
- 修改任何阈值、缺口规则或分析资格，都必须发布新的 contract/coverage version 或 parameter hash。
