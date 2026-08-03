from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


OPTION_KEYS = tuple("ABCDEF")


@dataclass(frozen=True)
class ParsedQuestion:
    sequence: int
    text: str
    options: dict[str, str]
    answer: Any
    question_type: str


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

        if options:
            choices = _split_choices(raw_answer)
            if not choices or any(choice not in options for choice in choices):
                raise ValueError(f"第 {row_number} 行答案必须对应已有的 A-F 选项")
            question_type = "multiple_choice" if len(set(choices)) > 1 else "single_choice"
            answer: Any = sorted(set(choices)) if question_type == "multiple_choice" else choices[0]
        else:
            try:
                Decimal(raw_answer)
                question_type = "numeric"
            except InvalidOperation:
                question_type = "short_answer"
            answer = raw_answer

        parsed.append(ParsedQuestion(sequence, text, options, answer, question_type))
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
