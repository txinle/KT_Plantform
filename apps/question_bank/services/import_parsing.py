from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .numeric import NonFiniteDecimalError, normalize_finite_decimal
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
    explanation: str = ""
    teacher_difficulty: str | None = None
    raw_primary_kc: str = ""
    raw_secondary_kcs: str = ""
    raw_tertiary_kcs: str = ""


@dataclass(frozen=True)
class ParsedKCMapping:
    sequence: int
    primary: str
    secondary: list[str]


@dataclass(frozen=True)
class ParsedCourseKC:
    sequence: int
    code: str
    name: str


@dataclass(frozen=True)
class ParsedQuestionKCTree:
    sequence: int
    root: str
    level_two: list[str]
    level_three: list[tuple[str, str]]


@dataclass(frozen=True)
class ParsedKCNode:
    code: str
    name: str
    description: str = ""
    status: str = "active"


@dataclass(frozen=True)
class ParsedQuestionKCRoot:
    stable_question_code: str
    primary_kc_code: str


@dataclass(frozen=True)
class ParsedQuestionKCRelation:
    stable_question_code: str
    parent_kc_code: str
    child_kc_code: str
    child_order: int


@dataclass(frozen=True)
class ParsedKCWorkbook:
    nodes: list[ParsedKCNode]
    roots: list[ParsedQuestionKCRoot]
    relations: list[ParsedQuestionKCRelation]


def _clean(value: Any) -> str:
    return " ".join(str("" if value is None else value).strip().split())


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
    raw = "" if value is None else str(value)
    answers = [_clean(item) for item in raw.replace("；", ";").split(";")]
    return list(dict.fromkeys(answer for answer in answers if answer))


def _require_exact_headers(
    rows: list[Mapping[str, Any]],
    expected: tuple[str, ...],
) -> None:
    if not rows:
        return
    actual = tuple(_clean(key) for key in rows[0].keys())
    if actual != expected:
        raise ValueError(f"表头必须严格为：{'、'.join(expected)}")


def _split_kcs(value: Any) -> list[str]:
    return [
        item
        for item in (_clean(part) for part in str(value or "").replace(";", "；").split("；"))
        if item
    ]


def parse_course_kc_rows(rows: Iterable[Mapping[str, Any]]) -> list[ParsedCourseKC]:
    from .course_kc import course_kc_code, normalize_course_kc_name

    materialized = list(rows)
    _require_exact_headers(materialized, ("序号", "知识点名称"))
    parsed: list[ParsedCourseKC] = []
    seen_sequences: set[int] = set()
    seen_names: set[str] = set()
    for row_number, row in enumerate(materialized, start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        sequence = _as_sequence(row.get("序号"), row_number)
        if sequence in seen_sequences:
            raise ValueError(f"序号 {sequence} 重复")
        name = _clean(row.get("知识点名称"))
        if not name:
            raise ValueError(f"第 {row_number} 行缺少知识点名称")
        normalized_name = normalize_course_kc_name(name)
        if normalized_name in seen_names:
            raise ValueError(f"第 {row_number} 行知识点名称重复：{name}")
        seen_sequences.add(sequence)
        seen_names.add(normalized_name)
        parsed.append(ParsedCourseKC(sequence, course_kc_code(sequence), name))
    if not parsed:
        raise ValueError("文件中没有可导入的课程 KC")
    return parsed


def parse_question_kc_tree_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[ParsedQuestionKCTree]:
    materialized = list(rows)
    _require_exact_headers(
        materialized,
        ("题目序号", "一级知识点", "二级知识点", "三级知识点"),
    )
    parsed: list[ParsedQuestionKCTree] = []
    seen_sequences: set[int] = set()
    for row_number, row in enumerate(materialized, start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        sequence = _as_sequence(row.get("题目序号"), row_number)
        if sequence in seen_sequences:
            raise ValueError(f"题目序号 {sequence} 重复")
        seen_sequences.add(sequence)

        roots = _split_kcs(row.get("一级知识点"))
        if len(roots) != 1:
            raise ValueError(f"第 {row_number} 行一级知识点必须且只能填写一个")
        level_two = _split_kcs(row.get("二级知识点"))
        level_three: list[tuple[str, str]] = []
        for relation in _split_kcs(row.get("三级知识点")):
            normalized_relation = relation.replace("＞", ">")
            if normalized_relation.count(">") != 1:
                raise ValueError(
                    f"第 {row_number} 行三级知识点必须使用二级KC>三级KC语法"
                )
            parent, child = (_clean(part) for part in normalized_relation.split(">", 1))
            if not parent or not child:
                raise ValueError(
                    f"第 {row_number} 行三级知识点必须使用二级KC>三级KC语法"
                )
            level_three.append((parent, child))
        parsed.append(ParsedQuestionKCTree(sequence, roots[0], level_two, level_three))
    if not parsed:
        raise ValueError("文件中没有可导入的 KC 对应关系")
    return parsed


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


def _parse_teacher_difficulty(value: Any, row_number: int) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        difficulty = Decimal(raw)
    except (InvalidOperation, ValueError):
        difficulty = Decimal("NaN")
    if (
        not difficulty.is_finite()
        or difficulty < 0
        or difficulty > 1
        or difficulty.as_tuple().exponent < -3
    ):
        raise ValueError(f"第 {row_number} 行难度必须为 0 到 1 之间且最多三位小数")
    return format(difficulty.quantize(Decimal("0.001")), "f")


def parse_question_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    sequence_floor: int = 0,
    allow_incomplete: bool = False,
) -> list[ParsedQuestion]:
    source_rows = [
        (row_number, row)
        for row_number, row in enumerate(rows, start=2)
        if any(_clean(value) for value in row.values())
    ]
    explicit_sequences: dict[int, int] = {}
    seen: set[int] = set()
    for row_number, row in source_rows:
        raw_sequence = _first(row, "序号", "题目序号", "sequence", "id")
        if not _clean(raw_sequence):
            continue
        sequence = _as_sequence(raw_sequence, row_number)
        if sequence in seen:
            raise ValueError(f"第 {row_number} 行的序号 {sequence} 重复")
        seen.add(sequence)
        explicit_sequences[row_number] = sequence

    parsed: list[ParsedQuestion] = []
    next_sequence = max([sequence_floor, *seen], default=sequence_floor) + 1
    for row_number, row in source_rows:
        sequence = explicit_sequences.get(row_number)
        if sequence is None:
            sequence = next_sequence
            next_sequence += 1
        text = _clean(_first(row, "题目", "题干", "question", "question_text"))
        if not text and not allow_incomplete:
            raise ValueError(f"第 {row_number} 行缺少题目")
        options = {
            key: _clean(_first(row, f"选项{key}", f"option_{key.lower()}", key))
            for key in OPTION_KEYS
        }
        options = {key: value for key, value in options.items() if value}
        raw_answer = _clean(_first(row, "答案", "正确答案", "answer", "correct_answer"))
        if not raw_answer and not allow_incomplete:
            raise ValueError(f"第 {row_number} 行缺少答案")

        explicit_type = _clean(_first(row, "题型", "题目类型", "question_type", "type"))
        accepted_answers = _split_accepted_answers(
            _first(row, "可接受答案", "其他可接受答案", "accepted_answers", "accepted_answer")
        )
        explanation = str(_first(row, "解析", "explanation") or "").strip()
        teacher_difficulty = _parse_teacher_difficulty(
            _first(row, "难度", "difficulty", "teacher_difficulty"),
            row_number,
        )
        if explicit_type:
            question_type = _explicit_question_type(explicit_type, row_number)
            if not allow_incomplete and question_type in {
                "numeric",
                "fill_blank",
                "true_false",
                "short_answer",
            } and options:
                raise ValueError(f"第 {row_number} 行非选择题不能填写选项")
            if question_type in {"single_choice", "multiple_choice"}:
                choice_answer = raw_answer
                if question_type == "multiple_choice" and accepted_answers:
                    choice_answer = ";".join([raw_answer, *accepted_answers])
                try:
                    answer = _parse_choice_answer(choice_answer, options, question_type, row_number)
                except ValueError:
                    if not allow_incomplete:
                        raise
                    answer = raw_answer
                else:
                    if question_type == "multiple_choice":
                        accepted_answers = []
            elif question_type == "numeric":
                try:
                    normalize_finite_decimal(raw_answer)
                except ValueError:
                    if not allow_incomplete:
                        raise ValueError(f"第 {row_number} 行数值题答案必须是有效数字") from None
                answer = raw_answer
            elif question_type == "true_false":
                answer = normalize_true_false_answer(raw_answer)
                if answer is None:
                    if not allow_incomplete:
                        raise ValueError(f"第 {row_number} 行判断题答案必须为正确或错误")
                    answer = raw_answer
            else:
                answer = raw_answer
        elif options:
            if not raw_answer:
                raise ValueError(f"第 {row_number} 行缺少题型，且无法根据空答案推断题型")
            choices = _split_choices(raw_answer)
            if not choices or any(choice not in options for choice in choices):
                if not allow_incomplete:
                    raise ValueError(f"第 {row_number} 行答案必须对应已有的 A-F 选项")
                question_type = "single_choice"
                answer = raw_answer
            else:
                question_type = "multiple_choice" if len(set(choices)) > 1 else "single_choice"
                answer = sorted(set(choices)) if question_type == "multiple_choice" else choices[0]
        else:
            if not raw_answer:
                raise ValueError(f"第 {row_number} 行缺少题型，且无法根据空答案推断题型")
            try:
                normalize_finite_decimal(raw_answer)
                question_type = "numeric"
            except NonFiniteDecimalError:
                raise ValueError(f"第 {row_number} 行数值题答案必须是有效数字") from None
            except ValueError:
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
                explanation,
                teacher_difficulty,
                _clean(_first(row, "一级知识点")),
                _clean(_first(row, "二级知识点")),
                _clean(_first(row, "三级知识点")),
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


def parse_kc_workbook(sheets: Mapping[str, list[Mapping[str, Any]]]) -> ParsedKCWorkbook:
    required = ("KC节点", "题目映射", "题目KC关系")
    missing = [name for name in required if name not in sheets]
    if missing:
        raise ValueError(f"工作簿缺少工作表：{', '.join(missing)}")

    nodes = []
    seen_codes = set()
    for row_number, row in enumerate(sheets["KC节点"], start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        code = _clean(_first(row, "kc_code"))
        name = _clean(_first(row, "kc_name"))
        status = _clean(_first(row, "status")) or "active"
        if not code or not name:
            raise ValueError(f"KC节点第 {row_number} 行缺少 kc_code 或 kc_name")
        if code in seen_codes:
            raise ValueError(f"KC节点存在重复 kc_code：{code}")
        if status not in {"active", "archived"}:
            raise ValueError(f"KC节点第 {row_number} 行 status 无效：{status}")
        seen_codes.add(code)
        nodes.append(ParsedKCNode(code, name, _clean(_first(row, "description")), status))

    roots = []
    seen_questions = set()
    for row_number, row in enumerate(sheets["题目映射"], start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        question_code = _clean(_first(row, "stable_question_code"))
        primary_code = _clean(_first(row, "primary_kc_code"))
        if not question_code or not primary_code:
            raise ValueError(f"题目映射第 {row_number} 行缺少题目代码或主 KC 代码")
        if question_code in seen_questions:
            raise ValueError(f"题目映射存在重复题目：{question_code}")
        seen_questions.add(question_code)
        roots.append(ParsedQuestionKCRoot(question_code, primary_code))

    relations = []
    for row_number, row in enumerate(sheets["题目KC关系"], start=2):
        if not any(_clean(value) for value in row.values()):
            continue
        question_code = _clean(_first(row, "stable_question_code"))
        parent_code = _clean(_first(row, "parent_kc_code"))
        child_code = _clean(_first(row, "child_kc_code"))
        try:
            order = int(_first(row, "child_order") or 1)
        except (TypeError, ValueError):
            raise ValueError(f"题目KC关系第 {row_number} 行 child_order 无效") from None
        if not question_code or not parent_code or not child_code:
            raise ValueError(f"题目KC关系第 {row_number} 行缺少必填字段")
        relations.append(ParsedQuestionKCRelation(question_code, parent_code, child_code, order))
    if not nodes or not roots:
        raise ValueError("KC v2 工作簿必须至少包含一个 KC 节点和一个题目映射")
    return ParsedKCWorkbook(nodes, roots, relations)
