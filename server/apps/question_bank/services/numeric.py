from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any


class NonFiniteDecimalError(ValueError):
    """Raised when a numeric literal parses but is not a finite Decimal."""


def normalize_finite_decimal(value: Any) -> Decimal:
    """Parse a numeric payload without narrowing valid Decimal values to float."""
    try:
        normalized = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        raise ValueError("数值题答案必须是有效数字") from None
    if not normalized.is_finite():
        raise NonFiniteDecimalError("数值题答案必须是有效数字")
    return normalized


def normalize_numeric_answer(value: Any) -> Decimal:
    """Parse a decimal or a literal fraction, never an executable expression."""
    text = str(value).strip()
    if '/' not in text:
        return normalize_finite_decimal(text)
    parts = text.split('/')
    if len(parts) != 2:
        raise ValueError('数值答案必须是数字或分数')
    numerator, denominator = (normalize_finite_decimal(part) for part in parts)
    if denominator == 0:
        raise ValueError('分母不能为零')
    try:
        result = numerator / denominator
    except DecimalException as error:
        raise ValueError('分数超出可计算范围') from error
    if not result.is_finite():
        raise ValueError('分数必须是有限数值')
    return result
