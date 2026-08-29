# 数据中心完整研究包一致性审计

> 生成时间：2026-08-29T14:59:01.067074+08:00  
> 代码基线：`82b6ecdc8cf3b703b51090ed8cc10325ea92f8ce`  
> 数据库：`sqlite`（隔离副本）  
> 课程：`KT101`；实验 ID `1`；版本 `1`

## 1. 结论

- 完整包总体结论：**结构一致，但统计可复现性与源数据质量存在异常**。
- 28 张目录表：21 张有观测，7 张为 `no_observation`。
- Manifest 行数、CSV 实际行数与 ORM 独立计数：存在不一致。
- 主外键/分类视图关系：未发现孤儿引用。
- 班级中心统计与冻结事实：**一致**。
- 冻结参与者可复现性：**异常**；应导出 3 人，实际 1 人，缺少 2 人。
- 值级数据质量：**存在异常**；2 项检查发现异常。
- 本报告不输出学生姓名、学号、答案或事件负载；原始完整包仍属于实名研究数据。

## 2. 审计边界与快照

| 项目 | 值 |
|---|---|
| ExportSnapshot | `65cff144-3910-42c4-8a28-5d9ec1fec658` |
| source_cutoff_at | `2026-08-29T06:59:00.777072+00:00` |
| event_high_watermark | `4` |
| scope_hash | `b6d1da4ddbe53fed5f682a0e7112a493e2845dbb1d5ba86a6ce05b2a6bada97a` |
| schema_manifest_hash | `51c29354c2f191c93480b09a6dbe71d1ab51eb0c4ccfcfcfb1bc3ad9b80510ef` |
| artifact SHA-256 | `38b92d1b58444e2bdfc33db98d8451c7d369ddb1175bcb893ea1d30e05cd3bc8` |
| 包内文件数 | 31 |
| 直接身份字段 | `student_number, student_name` |

## 3. 班级统计交叉核验

| 指标 | 班级中心 DTO | 独立数据库事实 | 结果 |
|---|---:|---:|---|
| roster 学生 | 3 | 3 | 一致 |
| 参与学生 | 1 | 1 | 一致 |
| 正确题数 | 1 | 1 | 一致 |
| 已评分客观题分母 | 1 | 1 | 一致 |
| 完成学生-实验单元 | 1 | 1 | 一致 |
| 应参与学生-实验单元 | 3 | 3 | 一致 |

说明：班级中心以当前 roster 为全集；研究导出的 `subjects.csv` 只包含冻结范围内实际形成会话的学生，两者不应强制相等。

### 统计可复现性缺口

`participant_snapshots.csv` 应保留冻结应参与者 3 人，当前只导出 1 人。缺少的 2 人均为未形成 Session 的冻结参与者。因而仅凭完整包无法重建完成率分母，属于导出语义异常，而不是正常的空值差异。

## 4. 全部表格汇总

| 表 | 中文含义 | 分层 | 行数 | 列数 | 状态 | ORM 对照 | 键缺失/重复 |
|---|---|---|---:|---:|---|---|---|
| `subjects` | 学生 | 核心事实 | 1 | 2 | observed | 一致 | 0/0 |
| `sessions` | 实验会话 | 核心事实 | 1 | 8 | observed | 一致 | 0/0 |
| `assignments` | 题目分配 | 核心事实 | 1 | 8 | observed | 一致 | 0/0 |
| `item_instances` | 题目呈现实例 | 核心事实 | 1 | 8 | observed | 一致 | 0/0 |
| `attempts` | 单题作答 | 核心事实 | 1 | 34 | observed | 一致 | 0/0 |
| `assessment_events` | 全部行为事件 | 原始事件 | 4 | 31 | observed | 一致 | 0/0 |
| `response_events` | 作答事件 | 事件分类视图 | 2 | 9 | observed | 一致 | 0/0 |
| `navigation_events` | 导航事件 | 事件分类视图 | 0 | 9 | no_observation | 一致 | 0/0 |
| `exposure_events` | 暴露事件 | 事件分类视图 | 0 | 9 | no_observation | 一致 | 0/0 |
| `telemetry_events` | 页面遥测 | 事件分类视图 | 2 | 9 | observed | 一致 | 0/0 |
| `simulation_events` | 仿真事件 | 事件分类视图 | 0 | 9 | no_observation | 一致 | 0/0 |
| `simulation_snapshots` | 仿真快照 | 扩展观测 | 0 | 10 | no_observation | 一致 | 0/0 |
| `item_snapshots` | 题目快照 | 设计元数据 | 1 | 7 | observed | 一致 | 0/0 |
| `kc_dictionary` | 知识点字典 | 设计元数据 | 1 | 2 | observed | 一致 | 0/0 |
| `item_kc_mapping` | 题目-KC对应 | 设计元数据 | 1 | 4 | observed | 一致 | 0/0 |
| `q_matrix` | Q矩阵 | 设计元数据 | 1 | 4 | observed | 一致 | 0/0 |
| `confidence_assignments` | Confidence信心 | 扩展观测 | 1 | 12 | observed | 一致 | 0/0 |
| `attempt_features` | 作答特征 | 可复现派生 | 1 | 11 | observed | 一致 | 0/0 |
| `kt_sequences` | KT序列 | 可复现派生 | 1 | 9 | observed | 一致 | 0/0 |
| `cd_response_matrix` | CD响应矩阵 | 可复现派生 | 1 | 8 | observed | 一致 | 0/0 |
| `calibration_features` | 信心校准 | 可复现派生 | 1 | 10 | observed | 一致 | 0/0 |
| `response_snapshots` | 答案快照 | 扩展观测 | 0 | 12 | no_observation | 一致 | 0/0 |
| `response_input_observations` | 稳定输入观测 | 事件投影视图 | 0 | 15 | no_observation | 一致 | 0/0 |
| `submissions` | 提交记录 | 核心事实 | 1 | 13 | observed | 一致 | 0/0 |
| `grade_records` | 评分记录 | 核心事实 | 1 | 18 | observed | 一致 | 0/0 |
| `condition_assignments` | 实验条件分配 | 设计元数据 | 0 | 14 | no_observation | 一致 | 0/0 |
| `class_snapshots` | 班级快照 | 设计元数据 | 1 | 7 | observed | 一致 | 0/0 |
| `participant_snapshots` | 版本参与者快照 | 设计元数据 | 1 | 5 | observed | 异常 | 0/0 |

## 5. 表间关系检查

| 检查 | 引用值数 | 孤儿数 | 结果 |
|---|---:|---:|---|
| assignments.session_id → sessions.session_id | 1 | 0 | 一致 |
| attempts.session_id → sessions.session_id | 1 | 0 | 一致 |
| attempts.question_snapshot_id → item_snapshots.question_snapshot_id | 1 | 0 | 一致 |
| assessment_events.session_id → sessions.session_id | 1 | 0 | 一致 |
| assessment_events.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| response_events.event_id → assessment_events.event_id | 2 | 0 | 一致 |
| navigation_events.event_id → assessment_events.event_id | 0 | 0 | 一致 |
| exposure_events.event_id → assessment_events.event_id | 0 | 0 | 一致 |
| telemetry_events.event_id → assessment_events.event_id | 2 | 0 | 一致 |
| simulation_events.event_id → assessment_events.event_id | 0 | 0 | 一致 |
| submissions.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| grade_records.submission_id → submissions.submission_id | 1 | 0 | 一致 |
| grade_records.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| confidence_assignments.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| item_kc_mapping.question_snapshot_id → item_snapshots.question_snapshot_id | 1 | 0 | 一致 |
| q_matrix.question_snapshot_id → item_snapshots.question_snapshot_id | 1 | 0 | 一致 |
| attempt_features.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| kt_sequences.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| cd_response_matrix.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| calibration_features.attempt_id → attempts.attempt_id | 1 | 0 | 一致 |
| participant_snapshots.class_snapshot_id → class_snapshots.class_snapshot_id | 1 | 0 | 一致 |

## 6. 值级数据质量检查

| 检查 | 异常数 | 状态 | 严重度 | 说明 |
|---|---:|---|---|---|
| 会话 started_at ≤ completed_at | 1 | 异常 | P1 | 完成时间不得早于开始时间；当前存在约 1.212 ms 的倒置。 |
| 提交 server_received_at ≤ committed_at | 0 | 正常 | — | 提交持久化时间顺序。 |
| 有效完成评分具有 graded_at | 1 | 异常 | P2 | completed/effective GradeRecord 应提供可审计评分时间。 |
| 跨表学生身份一致 | 0 | 正常 | — | 同一 session 在分配、作答和事件表中的身份字段一致。 |
| Attempt 权威评分投影一致 | 0 | 正常 | — | attempts 中 grade_* 字段与 effective GradeRecord 一致。 |
| 事件序号与高水位一致 | 0 | 正常 | — | 服务端事件序号唯一、单调且最大值等于 manifest 高水位。 |
| 事件分类视图精确 | 0 | 正常 | — | response/telemetry 分类表与权威事件表筛选结果完全相同。 |
| 派生运行血缘一致 | 0 | 正常 | — | 四张派生表 derivation_run_id 均等于 ExportSnapshot。 |

## 7. 分表字段与数据类型

类型来自本次 CSV 非空值的保守推断，不替代正式 Schema。`empty-only` 表示当前快照该列没有非空样本，不能据此判断其生产类型。

### `subjects` — 学生

- 路径：`02_core/subjects.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |

### `sessions` — 实验会话

- 路径：`02_core/sessions.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `session_id` | `integer` | 1 | 0 | 1 |
| `experiment_id` | `integer` | 1 | 0 | 1 |
| `version` | `integer` | 1 | 0 | 1 |
| `status` | `string` | 1 | 0 | 1 |
| `started_at` | `datetime` | 1 | 0 | 1 |
| `completed_at` | `datetime` | 1 | 0 | 1 |

### `assignments` — 题目分配

- 路径：`02_core/assignments.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `assignment_id` | `integer` | 1 | 0 | 1 |
| `session_id` | `integer` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `item_instance_id` | `uuid` | 1 | 0 | 1 |
| `assigned_sequence_index` | `integer` | 1 | 0 | 1 |

### `item_instances` — 题目呈现实例

- 路径：`02_core/item_instances.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `question_code` | `string` | 1 | 0 | 1 |
| `item_instance_id` | `uuid` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `presentation_variant` | `string` | 1 | 0 | 1 |
| `option_order` | `json` | 1 | 0 | 1 |
| `parameter_values` | `json` | 1 | 0 | 1 |
| `random_seed` | `empty-only` | 0 | 1 | 0 |
| `presentation_hash` | `string` | 1 | 0 | 1 |

### `attempts` — 单题作答

- 路径：`02_core/attempts.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `session_id` | `integer` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `item_instance_id` | `uuid` | 1 | 0 | 1 |
| `status` | `string` | 1 | 0 | 1 |
| `first_opened_at` | `datetime` | 1 | 0 | 1 |
| `last_opened_at` | `empty-only` | 0 | 1 | 0 |
| `visit_count` | `integer` | 1 | 0 | 1 |
| `draft_answer` | `empty-only` | 0 | 1 | 0 |
| `draft_saved_at` | `empty-only` | 0 | 1 | 0 |
| `final_answer` | `string` | 1 | 0 | 1 |
| `submitted_at` | `datetime` | 1 | 0 | 1 |
| `first_to_submit_duration_ms` | `integer` | 1 | 0 | 1 |
| `last_visit_to_submission_ms` | `empty-only` | 0 | 1 | 0 |
| `is_correct` | `empty-only` | 0 | 1 | 0 |
| `score` | `empty-only` | 0 | 1 | 0 |
| `first_open_sequence_index` | `empty-only` | 0 | 1 | 0 |
| `submission_sequence_index` | `empty-only` | 0 | 1 | 0 |
| `current_response_snapshot_id` | `empty-only` | 0 | 1 | 0 |
| `final_response_snapshot_id` | `empty-only` | 0 | 1 | 0 |
| `submission_event_id` | `integer` | 1 | 0 | 1 |
| `submission_grade_status` | `string` | 1 | 0 | 1 |
| `grade_version` | `integer` | 1 | 0 | 1 |
| `grade_is_correct` | `boolean` | 1 | 0 | 1 |
| `grade_score` | `decimal` | 1 | 0 | 1 |
| `grade_max_score` | `decimal` | 1 | 0 | 1 |
| `normalized_response` | `empty-only` | 0 | 1 | 0 |
| `grading_method` | `string` | 1 | 0 | 1 |
| `grading_version` | `empty-only` | 0 | 1 | 0 |
| `grader_version` | `empty-only` | 0 | 1 | 0 |
| `graded_at` | `empty-only` | 0 | 1 | 0 |

### `assessment_events` — 全部行为事件

- 路径：`03_raw_events/assessment_events.csv`
- 粒度/分层：原始事件；本次 4 行；Manifest 状态 `observed`。
- 行数核验：Manifest=4，CSV=4，ORM/规则=4，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 4 | 0 | 1 |
| `student_name` | `string` | 4 | 0 | 1 |
| `question_code` | `string` | 4 | 0 | 1 |
| `event_id` | `uuid` | 4 | 0 | 4 |
| `event_type` | `string` | 4 | 0 | 2 |
| `event_version` | `integer` | 4 | 0 | 1 |
| `session_id` | `integer` | 4 | 0 | 1 |
| `attempt_id` | `integer` | 4 | 0 | 1 |
| `client_instance_id` | `uuid` | 4 | 0 | 1 |
| `client_sequence_no` | `integer` | 4 | 0 | 4 |
| `server_ingest_sequence_no` | `integer` | 4 | 0 | 4 |
| `correlation_id` | `empty-only` | 0 | 4 | 0 |
| `server_received_at` | `datetime` | 4 | 0 | 4 |
| `capture_mode` | `string` | 4 | 0 | 1 |
| `quality_flags` | `json` | 4 | 0 | 1 |
| `payload` | `json` | 4 | 0 | 3 |
| `event_domain` | `string` | 4 | 0 | 2 |
| `actor_source` | `string` | 4 | 0 | 1 |
| `envelope_version` | `integer` | 4 | 0 | 1 |
| `assignment_id` | `integer` | 4 | 0 | 1 |
| `question_snapshot_id` | `integer` | 4 | 0 | 1 |
| `item_instance_id` | `uuid` | 4 | 0 | 1 |
| `client_wall_time_utc` | `empty-only` | 0 | 4 | 0 |
| `client_monotonic_ms` | `decimal` | 2 | 2 | 2 |
| `client_time_origin` | `datetime` | 2 | 2 | 1 |
| `time_sync_id` | `empty-only` | 0 | 4 | 0 |
| `estimated_clock_offset_ms` | `empty-only` | 0 | 4 | 0 |
| `estimated_rtt_ms` | `empty-only` | 0 | 4 | 0 |
| `estimated_occurred_at_utc` | `empty-only` | 0 | 4 | 0 |
| `server_committed_at` | `datetime` | 4 | 0 | 4 |
| `app_version` | `empty-only` | 0 | 4 | 0 |

### `response_events` — 作答事件

- 路径：`03_raw_events/response_events.csv`
- 粒度/分层：事件分类视图；本次 2 行；Manifest 状态 `observed`。
- 行数核验：Manifest=2，CSV=2，ORM/规则=2，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 2 | 0 | 1 |
| `student_name` | `string` | 2 | 0 | 1 |
| `question_code` | `string` | 2 | 0 | 1 |
| `event_id` | `uuid` | 2 | 0 | 2 |
| `event_type` | `string` | 2 | 0 | 1 |
| `session_id` | `integer` | 2 | 0 | 1 |
| `attempt_id` | `integer` | 2 | 0 | 1 |
| `server_received_at` | `datetime` | 2 | 0 | 2 |
| `payload` | `json` | 2 | 0 | 2 |

### `navigation_events` — 导航事件

- 路径：`03_raw_events/navigation_events.csv`
- 粒度/分层：事件分类视图；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `event_id` | `empty-only` | 0 | 0 | 0 |
| `event_type` | `empty-only` | 0 | 0 | 0 |
| `session_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `server_received_at` | `empty-only` | 0 | 0 | 0 |
| `payload` | `empty-only` | 0 | 0 | 0 |

### `exposure_events` — 暴露事件

- 路径：`04_learning_exposure/exposure_events.csv`
- 粒度/分层：事件分类视图；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `event_id` | `empty-only` | 0 | 0 | 0 |
| `event_type` | `empty-only` | 0 | 0 | 0 |
| `session_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `server_received_at` | `empty-only` | 0 | 0 | 0 |
| `payload` | `empty-only` | 0 | 0 | 0 |

### `telemetry_events` — 页面遥测

- 路径：`03_raw_events/telemetry_events.csv`
- 粒度/分层：事件分类视图；本次 2 行；Manifest 状态 `observed`。
- 行数核验：Manifest=2，CSV=2，ORM/规则=2，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 2 | 0 | 1 |
| `student_name` | `string` | 2 | 0 | 1 |
| `question_code` | `string` | 2 | 0 | 1 |
| `event_id` | `uuid` | 2 | 0 | 2 |
| `event_type` | `string` | 2 | 0 | 1 |
| `session_id` | `integer` | 2 | 0 | 1 |
| `attempt_id` | `integer` | 2 | 0 | 1 |
| `server_received_at` | `datetime` | 2 | 0 | 2 |
| `payload` | `json` | 2 | 0 | 1 |

### `simulation_events` — 仿真事件

- 路径：`03_raw_events/simulation_events.csv`
- 粒度/分层：事件分类视图；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `event_id` | `empty-only` | 0 | 0 | 0 |
| `event_type` | `empty-only` | 0 | 0 | 0 |
| `session_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `server_received_at` | `empty-only` | 0 | 0 | 0 |
| `payload` | `empty-only` | 0 | 0 | 0 |

### `simulation_snapshots` — 仿真快照

- 路径：`03_raw_events/simulation_snapshots.csv`
- 粒度/分层：扩展观测；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `snapshot_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `simulation_version` | `empty-only` | 0 | 0 | 0 |
| `state_metadata` | `empty-only` | 0 | 0 | 0 |
| `content_hash` | `empty-only` | 0 | 0 | 0 |
| `content_size` | `empty-only` | 0 | 0 | 0 |
| `created_at` | `empty-only` | 0 | 0 | 0 |

### `item_snapshots` — 题目快照

- 路径：`01_design_metadata/item_snapshots.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `question_code` | `string` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `version_id` | `integer` | 1 | 0 | 1 |
| `stable_code` | `string` | 1 | 0 | 1 |
| `question_type` | `string` | 1 | 0 | 1 |
| `primary_kc` | `string` | 1 | 0 | 1 |
| `all_kcs` | `json` | 1 | 0 | 1 |

### `kc_dictionary` — 知识点字典

- 路径：`01_design_metadata/kc_dictionary.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `kc_id` | `string` | 1 | 0 | 1 |
| `kc_name` | `string` | 1 | 0 | 1 |

### `item_kc_mapping` — 题目-KC对应

- 路径：`01_design_metadata/item_kc_mapping.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `question_code` | `string` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `primary_kc` | `string` | 1 | 0 | 1 |
| `all_kcs` | `json` | 1 | 0 | 1 |

### `q_matrix` — Q矩阵

- 路径：`01_design_metadata/q_matrix.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `question_code` | `string` | 1 | 0 | 1 |
| `question_snapshot_id` | `integer` | 1 | 0 | 1 |
| `primary_kc` | `string` | 1 | 0 | 1 |
| `all_kcs` | `json` | 1 | 0 | 1 |

### `confidence_assignments` — Confidence信心

- 路径：`05_self_report/confidence_assignments.csv`
- 粒度/分层：扩展观测；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `confidence_assignment_id` | `uuid` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `condition` | `string` | 1 | 0 | 1 |
| `assignment_unit` | `string` | 1 | 0 | 1 |
| `assignment_version` | `string` | 1 | 0 | 1 |
| `response_status` | `string` | 1 | 0 | 1 |
| `probability` | `empty-only` | 0 | 1 | 0 |
| `assigned_at` | `datetime` | 1 | 0 | 1 |
| `committed_at` | `empty-only` | 0 | 1 | 0 |

### `attempt_features` — 作答特征

- 路径：`06_derived/attempt_features.csv`
- 粒度/分层：可复现派生；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `derivation_run_id` | `uuid` | 1 | 0 | 1 |
| `derivation_version` | `string` | 1 | 0 | 1 |
| `is_correct` | `empty-only` | 0 | 1 | 0 |
| `score` | `empty-only` | 0 | 1 | 0 |
| `visit_count` | `integer` | 1 | 0 | 1 |
| `first_opened_at` | `datetime` | 1 | 0 | 1 |
| `submitted_at` | `datetime` | 1 | 0 | 1 |

### `kt_sequences` — KT序列

- 路径：`06_derived/kt_sequences.csv`
- 粒度/分层：可复现派生；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `derivation_run_id` | `uuid` | 1 | 0 | 1 |
| `derivation_version` | `string` | 1 | 0 | 1 |
| `sequence_index` | `integer` | 1 | 0 | 1 |
| `outcome` | `empty-only` | 0 | 1 | 0 |
| `primary_kc` | `string` | 1 | 0 | 1 |

### `cd_response_matrix` — CD响应矩阵

- 路径：`06_derived/cd_response_matrix.csv`
- 粒度/分层：可复现派生；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `derivation_run_id` | `uuid` | 1 | 0 | 1 |
| `derivation_version` | `string` | 1 | 0 | 1 |
| `response` | `string` | 1 | 0 | 1 |
| `all_kcs` | `json` | 1 | 0 | 1 |

### `calibration_features` — 信心校准

- 路径：`06_derived/calibration_features.csv`
- 粒度/分层：可复现派生；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `derivation_run_id` | `uuid` | 1 | 0 | 1 |
| `derivation_version` | `string` | 1 | 0 | 1 |
| `confidence_probability` | `empty-only` | 0 | 1 | 0 |
| `is_correct` | `empty-only` | 0 | 1 | 0 |
| `calibration_error` | `empty-only` | 0 | 1 | 0 |
| `condition` | `string` | 1 | 0 | 1 |

### `response_snapshots` — 答案快照

- 路径：`03_raw_events/response_snapshots.csv`
- 粒度/分层：扩展观测；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `snapshot_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `content` | `empty-only` | 0 | 0 | 0 |
| `content_hash` | `empty-only` | 0 | 0 | 0 |
| `content_length` | `empty-only` | 0 | 0 | 0 |
| `state_json` | `empty-only` | 0 | 0 | 0 |
| `state_hash` | `empty-only` | 0 | 0 | 0 |
| `schema_version` | `empty-only` | 0 | 0 | 0 |
| `created_at` | `empty-only` | 0 | 0 | 0 |

### `response_input_observations` — 稳定输入观测

- 路径：`03_raw_events/response_input_observations.csv`
- 粒度/分层：事件投影视图；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `empty-only` | 0 | 0 | 0 |
| `student_name` | `empty-only` | 0 | 0 | 0 |
| `question_code` | `empty-only` | 0 | 0 | 0 |
| `observation_id` | `empty-only` | 0 | 0 | 0 |
| `attempt_id` | `empty-only` | 0 | 0 | 0 |
| `client_instance_id` | `empty-only` | 0 | 0 | 0 |
| `client_sequence_no` | `empty-only` | 0 | 0 | 0 |
| `client_monotonic_ms` | `empty-only` | 0 | 0 | 0 |
| `edit_operation` | `empty-only` | 0 | 0 | 0 |
| `content_hash_before` | `empty-only` | 0 | 0 | 0 |
| `content_hash_after` | `empty-only` | 0 | 0 | 0 |
| `content_length_before` | `empty-only` | 0 | 0 | 0 |
| `content_length_after` | `empty-only` | 0 | 0 | 0 |
| `input_method` | `empty-only` | 0 | 0 | 0 |
| `observed_at` | `empty-only` | 0 | 0 | 0 |

### `submissions` — 提交记录

- 路径：`02_core/submissions.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `submission_id` | `integer` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `submission_token` | `uuid` | 1 | 0 | 1 |
| `final_response_snapshot_id` | `empty-only` | 0 | 1 | 0 |
| `grading_status` | `string` | 1 | 0 | 1 |
| `score` | `empty-only` | 0 | 1 | 0 |
| `is_correct` | `empty-only` | 0 | 1 | 0 |
| `client_clicked_at` | `empty-only` | 0 | 1 | 0 |
| `server_received_at` | `datetime` | 1 | 0 | 1 |
| `committed_at` | `datetime` | 1 | 0 | 1 |

### `grade_records` — 评分记录

- 路径：`02_core/grade_records.csv`
- 粒度/分层：核心事实；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `student_number` | `string` | 1 | 0 | 1 |
| `student_name` | `string` | 1 | 0 | 1 |
| `question_code` | `string` | 1 | 0 | 1 |
| `grade_id` | `integer` | 1 | 0 | 1 |
| `submission_id` | `integer` | 1 | 0 | 1 |
| `attempt_id` | `integer` | 1 | 0 | 1 |
| `version` | `integer` | 1 | 0 | 1 |
| `status` | `string` | 1 | 0 | 1 |
| `validity_status` | `string` | 1 | 0 | 1 |
| `score` | `decimal` | 1 | 0 | 1 |
| `max_score` | `decimal` | 1 | 0 | 1 |
| `is_correct` | `boolean` | 1 | 0 | 1 |
| `grading_method` | `string` | 1 | 0 | 1 |
| `grading_version` | `empty-only` | 0 | 1 | 0 |
| `graded_at` | `empty-only` | 0 | 1 | 0 |
| `supersedes_grade_id` | `empty-only` | 0 | 1 | 0 |
| `voided_at` | `empty-only` | 0 | 1 | 0 |
| `void_reason` | `empty-only` | 0 | 1 | 0 |

### `condition_assignments` — 实验条件分配

- 路径：`01_design_metadata/condition_assignments.csv`
- 粒度/分层：设计元数据；本次 0 行；Manifest 状态 `no_observation`。
- 行数核验：Manifest=0，CSV=0，ORM/规则=0，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `condition_assignment_id` | `empty-only` | 0 | 0 | 0 |
| `condition_id` | `empty-only` | 0 | 0 | 0 |
| `condition_key` | `empty-only` | 0 | 0 | 0 |
| `condition_label` | `empty-only` | 0 | 0 | 0 |
| `subject_id` | `empty-only` | 0 | 0 | 0 |
| `session_id` | `empty-only` | 0 | 0 | 0 |
| `assignment_unit_type` | `empty-only` | 0 | 0 | 0 |
| `assignment_algorithm` | `empty-only` | 0 | 0 | 0 |
| `assignment_version` | `empty-only` | 0 | 0 | 0 |
| `randomization_seed` | `empty-only` | 0 | 0 | 0 |
| `randomization_draw` | `empty-only` | 0 | 0 | 0 |
| `stratum` | `empty-only` | 0 | 0 | 0 |
| `assignment_probability` | `empty-only` | 0 | 0 | 0 |
| `assigned_at` | `empty-only` | 0 | 0 | 0 |

### `class_snapshots` — 班级快照

- 路径：`01_design_metadata/class_snapshots.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=1，结果 **一致**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `class_snapshot_id` | `uuid` | 1 | 0 | 1 |
| `version_id` | `integer` | 1 | 0 | 1 |
| `source_class_group_id` | `integer` | 1 | 0 | 1 |
| `name_snapshot` | `string` | 1 | 0 | 1 |
| `academic_year_snapshot` | `integer` | 1 | 0 | 1 |
| `capture_mode` | `string` | 1 | 0 | 1 |
| `created_at` | `datetime` | 1 | 0 | 1 |

### `participant_snapshots` — 版本参与者快照

- 路径：`01_design_metadata/participant_snapshots.csv`
- 粒度/分层：设计元数据；本次 1 行；Manifest 状态 `observed`。
- 行数核验：Manifest=1，CSV=1，ORM/规则=3，结果 **异常**。

| 字段 | 推断类型 | 非空 | 空值 | 非空唯一值 |
|---|---|---:|---:|---:|
| `participant_snapshot_id` | `integer` | 1 | 0 | 1 |
| `version_id` | `integer` | 1 | 0 | 1 |
| `student_id` | `integer` | 1 | 0 | 1 |
| `class_snapshot_id` | `uuid` | 1 | 0 | 1 |
| `created_at` | `datetime` | 1 | 0 | 1 |

## 8. 存储与运行异常

- 当前业务数据库在审计前没有 ExportJob 记录，但原 `media/exports` 下存在 **180 个目录、154 个文件、790107 字节**。这些文件不受当前数据库引用，属于孤儿产物。
- 本次没有删除这些文件。建议增加测试 MEDIA_ROOT 隔离与孤儿导出清理命令；清理前必须再次核对生产数据库引用。
- 当前演示数据覆盖很低：只有 1 个会话和 1 次作答。因此 0 行表的结构与空状态得到验证，但其非空字段值域尚未得到真实样本验证。

## 9. 整改优先级

| 优先级 | 问题 | 建议 |
|---|---|---|
| P1 | `participant_snapshots.csv` 遗漏 2 名未形成 Session 的冻结参与者 | 导出参与者快照时按所选版本和班级快照过滤，不得按已有 Session 学生反向裁剪；新增“3 人应参与、1 人参与”回归测试，并要求导出 3 条参与者快照。 |
| P1 | 会话完成时间早于开始时间约 1.212 ms | 修正演示数据/会话完成服务的赋值顺序；在服务层拒绝 `completed_at < started_at`，并增加数据审计规则。 |
| P2 | completed/effective 评分缺少 `graded_at` | 对已完成评分写入强制评分时间；评估历史数据回填策略，并增加 completed 状态的模型/服务校验。 |
| P2 | 业务库无 ExportJob，但媒体目录存在 180 个孤儿目录 | 测试环境使用独立临时 `MEDIA_ROOT`；增加只读孤儿扫描命令，清理前按数据库 artifact_path 和保留策略二次确认。 |
| P2 | 7 张表只有空表结构、没有非空样本 | 补建覆盖导航、暴露、仿真、答案快照、稳定输入、条件分配的验收数据，再复跑同一审计。 |

## 10. 判定边界

- 本次结论只适用于报告所列 SQLite 快照、课程、实验版本与代码提交。
- 本次没有验证 PostgreSQL 并发写入期间的快照一致性；那需要独立 PostgreSQL `REPEATABLE READ READ ONLY` 门禁。
- ‘一致’表示本次快照中导出、ORM 事实和页面统计口径一致，不代表空表对应功能已经获得充分实证覆盖。
