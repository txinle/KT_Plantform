# 核心数据字典

- `Attempt.first_opened_at`: 学生第一次进入题目的服务器时间。
- `Attempt.last_opened_at`: 正式提交前最后一次进入题目的服务器时间。
- `Attempt.last_visit_to_submission_ms`: `submitted_at - last_opened_at`。
- `Attempt.first_open_sequence_index`: 题目首次打开顺序。
- `Attempt.submission_sequence_index`: 题目正式提交顺序。
- `StudentQuestionAssignment.assigned_sequence_index`: 系统分配后的题目顺序。
- `QuestionVisitEvent`: 每次题号导航或恢复进入题目的明细事件。
- `SubmissionEvent.committed_at`: 数据库正式提交完成时间，作为正式提交时间。

## 实名可选字段 Excel

工作表 `KT数据` 的观测单位为“学生 × 题目”，每个 `Attempt` 一行。界面默认选择全部字段，服务端按下列固定顺序输出所选列：

- 身份：`student_number`（学号）、`student_name`（姓名）、`class_name`（会话发布时记录的班级）。
- 实验与会话：`experiment_id`、`experiment_version`、`session_id`、`session_status`。
- 题目与知识点：`question_code`、`question_type`、`primary_kc`、`all_kcs`、`teacher_difficulty`、`empirical_difficulty`。
- 顺序与行为：`assigned_sequence_index`、`first_open_sequence_index`、`submission_sequence_index`、`visit_count`。
- 时间：`first_opened_at`、`last_opened_at`、`submitted_at`、`first_to_submit_duration_ms`、`last_visit_to_submission_ms`。
- 作答结果：`final_answer`、`is_correct`、`score`、`attempt_status`。

学号、姓名和答案以 Excel 文本单元格写入，保留前导零且不执行以 `=`、`+`、`-` 或 `@` 开头的公式文本。该工作簿含直接身份信息，不属于匿名数据。

## 兼容导出

既有匿名 KT 与匿名导航 CSV 继续以 `anonymous_student_id` 标识学生，不新增学号或姓名列；身份对照 CSV 继续单独提供匿名编号与原始身份的映射。兼容 CSV 使用 UTF-8 BOM。
