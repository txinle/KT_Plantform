from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .scoring import normalize_true_false_answer


OPTION_KEYS = tuple("ABCDEF")

QUESTION_TYPE_ALIASES = {
    "single_choice": "single_choice",
    "single choice": "single_choice",
    "single-choice": "single_choice",
    "单选题": "single_choice",
    "单选": "single_choice",
    "multiple_choice": "multiple_choice",
    "multiple choice": "multiple_choice",
    "multiple-choice": "multiple_choice",
    "多选题": "multiple_choice",
    "多选": "multiple_choice",
    "numeric": "numeric",
    "number": "numeric",
    "数值题": "numeric",
    "数值": "numeric",
    "short_answer": "short_answer",
    "short answer": "short_answer",
    "short-answer": "short_answer",
    "简答题": "short_answer",
    "简答": "short_answer",
    "fill_blank": "fill_blank",
    "fill blank": "fill_blank",
    "fill-blank": "fill_blank",
    "填空题": "fill_blank",
    "填空": "fill_blank",
    "true_false": "true_false",
    "true false": "true_false",
    "true-false": "true_false",
    "判断题": "true_false",
    "判断": "true_false",
}


@dataclass(frozen=True)
class ParsedQuestion:
    sequence: int
    text: str
    options: dict[str, str]
    answer: Any
    question_type: str
    accepted_answers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedKCMapping:
    sequence: int
    primary: str
    secondary: list[str]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    normalized = {_clean(key).casefold(): value for key, value in row.items()}
    for key in keys:
        match = normalized.get(_clean(key).casefold())
        if match is not None:
            return match
    return ""


def _as_sequence(value: Any, row_number: int) -> int:
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"第 {row_number} 行的序号无效：{value!r}") from None
    if sequence <= 0:
        raise ValueError(f"第 {row_number} 行的序号必须大于 0")
    return sequence


def _split_choices(value: str) -> list[str]:
    cleaned = value.upper().replace("，", ",").replace("；", ",").replace(";", ",").replace(" ", "")
    if "," in cleaned:
        return [part for part in cleaned.split(",") if part]
    if cleaned and all(char in OPTION_KEYS for char in cleaned):
        return list(cleaned)
    return []


def _split_accepted_answers(value: Any) -> list[str]:
    answers = [_clean(item) for item in str(value or "").replace("；", ";").split(";")]
    return list(dict.fromkeys(answer for answer in answers if answer))


def _explicit_question_type(value: str, row_number: int) -> str:
    question_type = QUESTION_TYPE_ALIASES.get(_clean(value).casefold())
    if question_type is None:
        raise ValueError(f"第 {row_number} 行未知题型：{value!r}")
    return question_type


def _parse_choice_answer(
    raw_answer: str,
    options: dict[str, str],
    question_type: str,
    row_number: int,
) -> Any:
    choices = _split_choices(raw_answer)
    if not choices or any(choice not in options for choice in choices):
        raise ValueError(f"第 {row_number} 行答案必须对应已有的 A-F 选项")
    distinct_choices = sorted(set(choices))
    if question_type == "single_choice":
        if len(distinct_choices) != 1:
            raise ValueError(f"第 {row_number} 行单选题必须且只能填写一个正确选项")
        return distinct_choices[0]
    if len(distinct_choices) < 2:
        raise ValueError(f"第 {row_number} 行多选题至少需要两个正确选项")
    return distinct_choices


def parse_question_rows(rows: Iterable[Mapping[str, Any]]) -> list[ParsedQuestion]:
    parsed: list[ParsedQuestion] = []
    seen: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        sequence = _as_sequence(_first(row, "序号", "题目序号", "sequence", "id"), row_number)
        if sequence in seen:
            raise ValueError(f"第 {row_number} 行的序号 {sequence} 重复")
        seen.add(sequence)
        text = _clean(_first(row, "题目", "题干", "question", "question_text"))
        if not text:
            raise ValueError(f"第 {row_number} 行缺少题目")
        options = {
            key: _clean(_first(row, f"选项{key}", f"option_{key.lower()}", key))
            for key in OPTION_KEYS
        }
        options = {key: value for key, value in options.items() if value}
        raw_answer = _clean(_first(row, "答案", "正确答案", "answer", "correct_answer"))
        if not raw_answer:
            raise ValueError(f"第 {row_number} 行缺少答案")

        explicit_type = _clean(_first(row, "题型", "题目类型", "question_type", "type"))
        accepted_answers = _split_accepted_answers(
            _first(row, "可接受答案", "其他可接受答案", "accepted_answers", "accepted_answer")
        )
        if explicit_type:
            question_type = _explicit_question_type(explicit_type, row_number)
            if question_type in {
                "numeric",
                "fill_blank",
                "true_false",
                "short_answer",
            } and options:
                raise ValueError(f"第 {row_number} 行非选择题不能填写选项")
            if question_type in {"single_choice", "multiple_choice"}:
                answer = _parse_choice_answer(raw_answer, options, question_type, row_number)
            elif question_type == "numeric":
                try:
                    numeric_answer = Decimal(raw_answer)
                except InvalidOperation:
                    raise ValueError(f"第 {row_number} 行数值题答案必须是有效数字") from None
                # Decimal.is_finite() is the exact equivalent of math.isfinite
                # here and does not narrow large valid Decimal values to float.
                if not numeric_answer.is_finite():
                    raise ValueError(f"第 {row_number} 行数值题答案必须是有效数字")
                answer = raw_answer
            elif question_type == "true_false":
                answer = normalize_true_false_answer(raw_answer)
                if answer is None:
                    raise ValueError(f"第 {row_number} 行判断题答案必须为正确或错误")
            else:
                answer = raw_answer
        elif options:
            choices = _split_choices(raw_answer)
            if not choices or any(choice not in options for choice in choices):
                raise ValueError(f"第 {row_number} 行答案必须对应已有的 A-F 选项")
            question_type = "multiple_choice" if len(set(choices)) > 1 else "single_choice"
            answer = sorted(set(choices)) if question_type == "multiple_choice" else choices[0]
        else:
            try:
                Decimal(raw_answer)
                question_type = "numeric"
            except InvalidOperation:
                question_type = "short_answer"
            answer = raw_answer

        parsed.append(
            ParsedQuestion(
                sequence,
                text,
                options,
                answer,
                question_type,
                accepted_answers,
            )
        )
    if not parsed:
        raise ValueError("文件中没有可导入的题目")
    return parsed


def parse_kc_rows(rows: Iterable[Mapping[str, Any]]) -> list[ParsedKCMapping]:
    parsed: list[ParsedKCMapping] = []
    seen: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        sequence = _as_sequence(_first(row, "题目序号", "序号", "sequence"), row_number)
        if sequence in seen:
            raise ValueError(f"第 {row_number} 行的题目序号 {sequence} 重复")
        seen.add(sequence)
        primary = _clean(_first(row, "主知识点", "primary_kc", "primary"))
        if not primary:
            raise ValueError(f"第 {row_number} 行缺少主知识点")
        raw_secondary = _clean(_first(row, "次知识点", "secondary_kcs", "secondary"))
        secondary = [
            _clean(item)
            for item in raw_secondary.replace("；", ";").split(";")
            if _clean(item) and _clean(item) != primary
        ]
        parsed.append(ParsedKCMapping(sequence, primary, list(dict.fromkeys(secondary))))
    if not parsed:
        raise ValueError("文件中没有可导入的 KC 对应关系")
    return parsed
