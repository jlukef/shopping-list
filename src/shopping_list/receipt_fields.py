"""Shared field validation for receipt data — manual entry (5a) and AI extraction (5b).

Kept in its own module (not receipts_service.py) so receipt_extraction.py can import
these without a circular import: receipts_service.py imports the extraction
orchestrator, and both need the same validation rules for the same columns.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any

MAX_ITEM_NAME_LENGTH = 200
MAX_UNIT_LENGTH = 32
MAX_QUANTITY = 1_000_000.0
MAX_MONEY_PENNIES = 100_000_000


def as_item_name(value: Any) -> str:
    """Required name — used for manually-typed rows, which have no raw_text fallback."""
    name = str(value or "").strip()
    if not name:
        raise ValueError("Item name is required")
    if len(name) > MAX_ITEM_NAME_LENGTH:
        raise ValueError(f"Item name must be {MAX_ITEM_NAME_LENGTH} characters or fewer")
    return name


def as_optional_name(value: Any) -> str | None:
    """Nullable name — extraction may legitimately not be able to read one."""
    name = str(value or "").strip()
    if not name:
        return None
    if len(name) > MAX_ITEM_NAME_LENGTH:
        raise ValueError(f"Item name must be {MAX_ITEM_NAME_LENGTH} characters or fewer")
    return name


def as_unit(value: Any) -> str | None:
    unit = str(value or "").strip()
    if len(unit) > MAX_UNIT_LENGTH:
        raise ValueError(f"Unit must be {MAX_UNIT_LENGTH} characters or fewer")
    return unit or None


def as_quantity(value: Any, *, default: float | None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        quantity = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Quantity must be a number") from exc
    if not math.isfinite(quantity) or quantity <= 0 or quantity > MAX_QUANTITY:
        raise ValueError("Quantity must be a positive, finite number")
    return quantity


def as_optional_money(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Money must be a whole number of pennies")
    try:
        pennies = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Money must be a whole number of pennies") from exc
    if str(value).strip() not in {str(pennies), f"{pennies}.0"}:
        raise ValueError("Money must be a whole number of pennies")
    if pennies < 0 or pennies > MAX_MONEY_PENNIES:
        raise ValueError("Money value is outside the allowed range")
    return pennies


def as_optional_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Date must be a valid YYYY-MM-DD date") from exc
    if parsed > date.today():
        raise ValueError("Date cannot be in the future")
    return parsed.isoformat()


def as_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value
