# 实名数据表示统一（2026-08-27）

本变更取代先前关于研究导出使用 `subject_id`、`course_subject_id`、`study_subject_id` 或匿名 CSV 字段的约定；历史设计记录保留原文以说明演进，不再构成当前实现契约。

- 所有学生相关导出行使用 `student_number` 与 `student_name` 作为对外学生标识。内部 UUID 和关系主键仍可用于数据库关联与审计，但不作为导出中的学生标识。
- 所有题目相关导出以 `QuestionSnapshot.code` 输出为 `question_code`，这是题库唯一编号和主关联键；`question_snapshot_id`、`stable_code` 和其他内部编号仅用于辅助追溯。同一实验版本内重复或空的 `question_code` 会阻止导出。
- 课程数据中心按“实验 → 版本 → 班级快照 → 学生 → 会话 → 状态”筛选。班级来自 `ExperimentSession.class_snapshot_name`，没有班级快照时显示并筛选为“未记录班级”。
- 既有 CSV 路径不变，但它们现在输出实名列和 `question_code`。学生名册为 `student_number,student_name,class_name`。
- Manifest 升级为版本 2，写入直接身份列、题目主标识和冻结的筛选范围。所有课程协作教师在课程授权范围内均可生成和下载导出，生成和下载均有审计记录。

这些文件包含直接身份信息；需要对外共享时，应由使用者在本平台之外完成研究方案允许的数据处理与去标识化。
