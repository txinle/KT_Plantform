# 核心数据字典

- `Attempt.first_opened_at`: 学生第一次进入题目的服务器时间。
- `Attempt.last_opened_at`: 正式提交前最后一次进入题目的服务器时间。
- `Attempt.last_visit_to_submission_ms`: `submitted_at - last_opened_at`。
- `Attempt.first_open_sequence_index`: 题目首次打开顺序。
- `Attempt.submission_sequence_index`: 题目正式提交顺序。
- `StudentQuestionAssignment.assigned_sequence_index`: 系统分配后的题目顺序。
- `QuestionVisitEvent`: 每次题号导航或恢复进入题目的明细事件。
- `SubmissionEvent.committed_at`: 数据库正式提交完成时间，作为正式提交时间。
