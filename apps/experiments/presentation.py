"""Read-only presentation values shared by teacher HTML and Word reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ObjectDoesNotExist

from apps.question_bank.services.scoring import normalize_true_false_answer


QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "numeric": "数值题",
    "short_answer": "简答题",
    "fill_blank": "填空题",
    "true_false": "判断题",
}


@dataclass(frozen=True)
class OptionPresentation:
    key: str
    text: str

    @property
    def display(self) -> str:
        return f"{self.key}：{self.text}" if self.key else self.text


@dataclass(frozen=True)
class SessionQuestionPresentation:
    assigned_sequence_index: int
    stable_code: str
    question_type: str
    question_type_label: str
    text: str
    options: tuple[OptionPresentation, ...]
    student_answer: str
    correct_answer: str
    answer_status: str


@dataclass(frozen=True)
class SessionReportPresentation:
    student_name: str
    student_number: str
    class_snapshot_name: str
    version_number: int
    session_status: str
    questions: tuple[SessionQuestionPresentation, ...]

    @property
    def question_count(self) -> int:
        return len(self.questions)


def _xml_safe_text(value: str) -> str:
    """Remove control characters that Django/Word cannot safely render."""

    return "".join(
        character
        for character in value
        if character in "\t\n\r" or ord(character) >= 32
    )


def display_text(value: Any) -> str:
    """Return plain text without marking user-controlled values safe for HTML."""

    if value is None:
        return ""
    if isinstance(value, str):
        rendered = value
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        rendered = str(value)
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            rendered = str(value)
    return _xml_safe_text(rendered)


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def options_for_snapshot(snapshot) -> tuple[OptionPresentation, ...]:
    """Normalize immutable snapshot options without trusting their JSON shape."""

    if snapshot.question_type == "true_false":
        return (
            OptionPresentation(key="正确", text="正确"),
            OptionPresentation(key="错误", text="错误"),
        )

    raw_options = snapshot.options
    if not isinstance(raw_options, (list, tuple)):
        return ()

    options = []
    for raw_option in raw_options:
        if isinstance(raw_option, Mapping):
            key = display_text(raw_option.get("key", "")).strip()
            text = display_text(raw_option.get("text", ""))
        else:
            key = ""
            text = display_text(raw_option)
        options.append(OptionPresentation(key=key, text=text))
    return tuple(options)


def _choice_keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = value.replace("，", ",").replace("；", ",").replace(";", ",")
        parts = normalized.split(",") if "," in normalized else list(normalized)
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]

    keys = []
    for part in parts:
        key = display_text(part).strip().upper()
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def format_answer(
    question_type: str,
    value: Any,
    options: tuple[OptionPresentation, ...],
) -> str:
    """Format one immutable answer consistently for HTML and DOCX output."""

    if value is None:
        return ""

    if question_type == "true_false":
        normalized = normalize_true_false_answer(value)
        if normalized is not None:
            return "正确" if normalized else "错误"
        return display_text(value)

    if question_type in {"single_choice", "multiple_choice"}:
        keys = _choice_keys(value)
        if not keys:
            return ""
        selected = set(keys)
        known = {
            option.key.strip().upper()
            for option in options
            if option.key.strip()
        }
        displays = [
            option.display
            for option in options
            if option.key.strip().upper() in selected
        ]
        displays.extend(
            f"{key}（快照中未找到该选项）"
            for key in keys
            if key not in known
        )
        return "；".join(displays)

    return display_text(value)


def _attempt_for(assignment):
    try:
        return assignment.attempt
    except ObjectDoesNotExist:
        return None


def present_assignment(assignment) -> SessionQuestionPresentation:
    snapshot = assignment.snapshot
    options = options_for_snapshot(snapshot)
    attempt = _attempt_for(assignment)

    if attempt is not None and attempt.status == "submitted":
        student_answer = format_answer(snapshot.question_type, attempt.final_answer, options)
        if not student_answer:
            student_answer = "（空）"
        if attempt.is_correct is True:
            answer_status = "回答正确"
        elif attempt.is_correct is False:
            answer_status = "回答错误"
        else:
            answer_status = "已提交，正误未判定"
    elif attempt is not None and _has_answer(attempt.draft_answer):
        student_answer = format_answer(snapshot.question_type, attempt.draft_answer, options)
        answer_status = "草稿，未正式提交"
    else:
        student_answer = "未作答"
        answer_status = "未作答"

    stable_code = display_text(snapshot.stable_code).strip() or display_text(snapshot.code)
    correct_answer = format_answer(snapshot.question_type, snapshot.correct_answer, options)
    return SessionQuestionPresentation(
        assigned_sequence_index=assignment.assigned_sequence_index,
        stable_code=stable_code,
        question_type=snapshot.question_type,
        question_type_label=QUESTION_TYPE_LABELS.get(snapshot.question_type, snapshot.question_type),
        text=display_text(snapshot.text),
        options=options,
        student_answer=student_answer,
        correct_answer=correct_answer or "（未设置）",
        answer_status=answer_status,
    )


def build_session_report_presentation(session, assignments) -> SessionReportPresentation:
    """Build immutable, display-safe report data without changing assessment data."""

    questions = tuple(
        present_assignment(assignment)
        for assignment in sorted(assignments, key=lambda item: item.assigned_sequence_index)
    )
    return SessionReportPresentation(
        student_name=display_text(session.student.shown_name),
        student_number=display_text(session.student.student_number),
        class_snapshot_name=display_text(session.class_snapshot_name),
        version_number=session.version.version_number,
        session_status=session.get_status_display(),
        questions=questions,
    )
