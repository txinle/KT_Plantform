import json


FIELD_GROUPS = (
    ("身份信息", (("student_number", "学号"), ("student_name", "姓名"), ("class_name", "班级"))),
    (
        "课程快照",
        (
            ("course_name", "课程名称"),
            ("course_code", "课程代码"),
            ("course_term", "学期"),
        ),
    ),
    (
        "实验与会话",
        (
            ("experiment_id", "实验 ID"),
            ("experiment_version", "实验版本"),
            ("session_id", "会话 ID"),
            ("session_status", "会话状态"),
        ),
    ),
    (
        "题目与知识点",
        (
            ("question_code", "题目标识"),
            ("question_type", "题型"),
            ("primary_kc", "主 KC"),
            ("all_kcs", "全部 KC"),
            ("teacher_difficulty", "教师难度"),
            ("empirical_difficulty", "经验难度"),
        ),
    ),
    (
        "顺序与行为",
        (
            ("assigned_sequence_index", "分配顺序"),
            ("first_open_sequence_index", "首次打开顺序"),
            ("submission_sequence_index", "提交顺序"),
            ("visit_count", "访问次数"),
        ),
    ),
    (
        "时间",
        (
            ("first_opened_at", "首次打开时间"),
            ("last_opened_at", "最后打开时间"),
            ("submitted_at", "提交时间"),
            ("first_to_submit_duration_ms", "首次打开至提交时长（毫秒）"),
            ("last_visit_to_submission_ms", "最后访问至提交时长（毫秒）"),
        ),
    ),
    (
        "作答结果",
        (
            ("final_answer", "最终答案"),
            ("is_correct", "是否正确"),
            ("score", "得分"),
            ("attempt_status", "作答状态"),
        ),
    ),
)

FIELD_LABELS = {
    key: label
    for _group_label, fields in FIELD_GROUPS
    for key, label in fields
}


def _answer(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def field_values(attempt):
    snapshot = attempt.assignment.snapshot
    session = attempt.session
    student = session.student
    version = session.version
    return {
        "student_number": student.student_number,
        "student_name": student.shown_name,
        "class_name": session.class_snapshot_name,
        "course_name": version.course_name_snapshot,
        "course_code": version.course_code_snapshot,
        "course_term": version.course_term_snapshot,
        "experiment_id": session.version.experiment_id,
        "experiment_version": session.version.version_number,
        "session_id": session.pk,
        "session_status": session.status,
        "question_code": snapshot.code,
        "question_type": snapshot.question_type,
        "primary_kc": snapshot.primary_kc,
        "all_kcs": ";".join(snapshot.all_kcs),
        "teacher_difficulty": snapshot.teacher_difficulty,
        "empirical_difficulty": snapshot.empirical_difficulty,
        "assigned_sequence_index": attempt.assignment.assigned_sequence_index,
        "first_open_sequence_index": attempt.first_open_sequence_index,
        "submission_sequence_index": attempt.submission_sequence_index,
        "visit_count": attempt.visit_count,
        "first_opened_at": attempt.first_opened_at.isoformat() if attempt.first_opened_at else None,
        "last_opened_at": attempt.last_opened_at.isoformat() if attempt.last_opened_at else None,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "first_to_submit_duration_ms": attempt.first_to_submit_duration_ms,
        "last_visit_to_submission_ms": attempt.last_visit_to_submission_ms,
        "final_answer": _answer(attempt.final_answer),
        "is_correct": attempt.is_correct,
        "score": attempt.score,
        "attempt_status": attempt.status,
    }
