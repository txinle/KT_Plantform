"""Generate an untracked, multi-page DOCX fixture for session-report visual QA."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.experiments.presentation import (  # noqa: E402
    OptionPresentation,
    SessionQuestionPresentation,
    SessionReportPresentation,
)
from apps.experiments.session_report import build_session_report_document  # noqa: E402


LONG_STEM = (
    "请结合本次学习过程，完整说明为何实际分配顺序必须与首次访问顺序和提交顺序分别记录。"
    "回答时应明确区分发布快照、学生会话和单题作答三个层级，并用自己的语言解释这些记录"
    "在复现与研究解释中的作用。"
)


def build_fixture_report() -> SessionReportPresentation:
    questions = [
        SessionQuestionPresentation(
            assigned_sequence_index=1,
            stable_code="KT-C-2026-001",
            question_type="multiple_choice",
            question_type_label="多选题",
            text=LONG_STEM,
            options=(
                OptionPresentation("A", "发布后的题干和选项应以快照为准。"),
                OptionPresentation("B", "学生实际收到的题目由该会话的分配记录决定。"),
                OptionPresentation("C", "首次访问顺序可以替代分配顺序，无需单独保留。"),
                OptionPresentation("D", "提交顺序反映正式提交发生的先后，而不是题目发放顺序。"),
            ),
            student_answer=(
                "A：发布后的题干和选项应以快照为准。；"
                "B：学生实际收到的题目由该会话的分配记录决定。；"
                "D：提交顺序反映正式提交发生的先后，而不是题目发放顺序。"
            ),
            correct_answer=(
                "A：发布后的题干和选项应以快照为准。；"
                "B：学生实际收到的题目由该会话的分配记录决定。；"
                "D：提交顺序反映正式提交发生的先后，而不是题目发放顺序。"
            ),
            answer_status="回答正确",
        ),
        SessionQuestionPresentation(
            assigned_sequence_index=2,
            stable_code="KT-C-2026-002",
            question_type="true_false",
            question_type_label="判断题",
            text="判断题：学生保存草稿后，系统已经完成该题的正式提交。",
            options=(
                OptionPresentation("", "正确"),
                OptionPresentation("", "错误"),
            ),
            student_answer="正确",
            correct_answer="错误",
            answer_status="草稿，未正式提交",
        ),
    ]
    for index in range(3, 15):
        questions.append(
            SessionQuestionPresentation(
                assigned_sequence_index=index,
                stable_code=f"KT-C-2026-{index:03d}",
                question_type="short_answer",
                question_type_label="简答题",
                text=f"第 {index} 道长中文题：{LONG_STEM}",
                options=(),
                student_answer="未作答",
                correct_answer="应根据本题题干作答。",
                answer_status="未作答",
            )
        )
    return SessionReportPresentation(
        student_name="王同学",
        student_number="S-2026-001",
        class_snapshot_name="2026 秋季实验班（发布快照）",
        version_number=7,
        session_status="已完成",
        questions=tuple(questions),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Target .docx path")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_session_report_document(build_fixture_report()))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
