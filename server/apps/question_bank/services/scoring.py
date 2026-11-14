from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from .numeric import normalize_finite_decimal, normalize_numeric_answer


def _normalized_text(value: Any, *, case_sensitive=False) -> str:
    """Trim, casefold, then collapse every run of internal whitespace."""
    text = str(value or "").strip()
    return " ".join((text if case_sensitive else text.casefold()).split())


def normalize_true_false_answer(value: Any) -> bool | None:
    """Map the fixed judgement responses to their JSON boolean representation."""
    if isinstance(value, bool):
        return value
    normalized = _normalized_text(value)
    if normalized in {"true", "正确"}:
        return True
    if normalized in {"false", "错误"}:
        return False
    return None


def _choice_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        cleaned = value.replace("，", ",").replace("；", ",").replace(";", ",")
        parts = cleaned.split(",") if "," in cleaned else list(cleaned)
    elif isinstance(value, Iterable):
        parts = list(value)
    else:
        parts = [value]
    return {str(item).strip().upper() for item in parts if str(item).strip()}


def _decimal(value: Any, *, answer=False) -> Decimal | None:
    try:
        return normalize_numeric_answer(value) if answer else normalize_finite_decimal(value)
    except ValueError:
        return None


def score_payload(
    question_type: str,
    correct_answer: Any,
    submitted_answer: Any,
    *,
    accepted_answers: Iterable[Any] | None = None,
    absolute_tolerance: Any = None,
    relative_tolerance: Any = None,
    max_score: Any = Decimal("1"),
    case_sensitive: bool = False,
    question_text: str = '',
) -> tuple[bool, Decimal]:
    """Return binary correctness and its frozen question weight."""
    maximum = normalize_finite_decimal(max_score)
    if maximum < Decimal('0.01') or maximum > Decimal('999.99') or maximum != maximum.quantize(Decimal('0.01')):
        raise ValueError('分值必须为 0.01 至 999.99，最多两位小数。')
    is_correct = False

    if question_type == "single_choice":
        is_correct = _normalized_text(correct_answer).upper() == _normalized_text(submitted_answer).upper()
    elif question_type == "multiple_choice":
        is_correct = bool(_choice_set(correct_answer)) and _choice_set(correct_answer) == _choice_set(submitted_answer)
    elif question_type == "numeric":
        expected = _decimal(correct_answer, answer=True)
        observed = _decimal(submitted_answer, answer=True)
        if expected is not None and observed is not None:
            difference = abs(observed - expected)
            absolute = _decimal(absolute_tolerance)
            relative = _decimal(relative_tolerance)
            checks: list[bool] = []
            if absolute is not None:
                checks.append(difference <= absolute)
            if relative is not None:
                scale = abs(expected) if expected != 0 else Decimal("1")
                checks.append(difference <= relative * scale)
            is_correct = any(checks) if checks else difference == 0
    elif question_type == "fill_blank":
        from .legacy_fill import fill_answer_matches
        is_correct = any(fill_answer_matches(candidate, submitted_answer,
            question_text=question_text, case_sensitive=case_sensitive)
            for candidate in [correct_answer, *(accepted_answers or [])])
    elif question_type == "short_answer":
        candidates = [correct_answer, *(accepted_answers or [])]
        submitted = _normalized_text(submitted_answer, case_sensitive=case_sensitive)
        is_correct = bool(submitted) and submitted in {_normalized_text(item, case_sensitive=case_sensitive) for item in candidates}
    elif question_type == "true_false":
        expected = normalize_true_false_answer(correct_answer)
        observed = normalize_true_false_answer(submitted_answer)
        is_correct = expected is not None and observed is not None and expected is observed
    else:
        raise ValueError(f"Unsupported question type: {question_type}")

    return is_correct, maximum if is_correct else Decimal("0")
