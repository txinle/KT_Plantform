# 核心数据字典

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

匿名 KT 与匿名导航 CSV 的 URL 和 headers 保持不变：`/analytics/experiments/{experiment_id}/kt.csv` 与 `/analytics/experiments/{experiment_id}/navigation.csv` 继续以 `anonymous_student_id` 标识学生，不新增学号、姓名、课程永久编号或其他实名列。身份对照 CSV 仍在 `/analytics/experiments/{experiment_id}/identity.csv` 单独提供匿名编号与原始身份的既有映射 headers。兼容 CSV 使用 UTF-8 BOM。
