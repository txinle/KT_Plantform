from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


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


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def score_payload(
    question_type: str,
    correct_answer: Any,
    submitted_answer: Any,
    *,
    accepted_answers: Iterable[Any] | None = None,
    absolute_tolerance: Any = None,
    relative_tolerance: Any = None,
) -> tuple[bool, Decimal]:
    """Return binary correctness and a 0/1 score for the MVP scoring rules."""
    is_correct = False

    if question_type == "single_choice":
        is_correct = _normalized_text(correct_answer).upper() == _normalized_text(submitted_answer).upper()
    elif question_type == "multiple_choice":
        is_correct = bool(_choice_set(correct_answer)) and _choice_set(correct_answer) == _choice_set(submitted_answer)
    elif question_type == "numeric":
        expected = _decimal(correct_answer)
        observed = _decimal(submitted_answer)
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
    elif question_type == "short_answer":
        candidates = [correct_answer, *(accepted_answers or [])]
        submitted = _normalized_text(submitted_answer)
        is_correct = bool(submitted) and submitted in {_normalized_text(item) for item in candidates}
    else:
        raise ValueError(f"Unsupported question type: {question_type}")

    return is_correct, Decimal("1") if is_correct else Decimal("0")
