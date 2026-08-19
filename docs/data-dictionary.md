# 核心数据字典

- `Attempt.first_opened_at`: 学生第一次进入题目的服务器时间。
- `Attempt.last_opened_at`: 正式提交前最后一次进入题目的服务器时间。
- `Attempt.last_visit_to_submission_ms`: `submitted_at - last_opened_at`。
- `Attempt.first_open_sequence_index`: 题目首次打开顺序。
- `Attempt.submission_sequence_index`: 题目正式提交顺序。
- `StudentQuestionAssignment.assigned_sequence_index`: 系统分配后的题目顺序。
- `QuestionVisitEvent`: 每次题号导航或恢复进入题目的明细事件。
- `SubmissionEvent.committed_at`: 数据库正式提交完成时间，作为正式提交时间。

## 课程班级与学生账号

- `User.student_number`：平台级唯一学号；同一账号可以加入多门课程。
- `CourseEnrollment`：学生在单门课程中的唯一选课关系，`class_group` 表示该课程下的班级归属。
- 手动添加和 Excel 导入复用同学号、同姓名的既有账号；新账号密码规则为 `S + 原始学号`。
- 从其他课程班级复制时只复制有效 `CourseEnrollment`；目标课程已有选课的学生被跳过，不复制账号、不改密码、不自动转班。
- 实验分配时保存 `ExperimentStudent.class_snapshot_name`；班级后续改名不改变该快照。

## 课程 KC 与章节视图

- `Concept.course`：课程 KC 的所属课程；同一课程按 `normalized_name` 唯一。
- `QuestionConcept`：题目与课程 KC 的映射；每题最多一个 `is_primary=True` 的主 KC，并可关联多个次 KC。
- “课程 KC 总库”统计课程内全部题库和题目关联；“本题库章节 KC”只统计当前题库引用的同一批课程 KC，不是独立数据实体。
- `QuestionSnapshot.primary_kc` 与 `QuestionSnapshot.all_kcs` 在实验发布时复制 KC 名称；后续 KC 重命名不回写历史快照。

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
