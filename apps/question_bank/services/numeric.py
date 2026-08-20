from __future__ import annotations

from decimal import Decimal, InvalidOperation
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
