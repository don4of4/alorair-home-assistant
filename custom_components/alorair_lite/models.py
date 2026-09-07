"""Interpret observed vendor fields without inventing device telemetry."""

import math
import re
from datetime import datetime
from typing import Any

FAULT_BITS = {0: "E1", 1: "L0", 2: "HI", 3: "E5", 4: "E4", 6: "E3", 7: "E7"}


def normalize_mac(value: Any) -> str | None:
    """Normalize only complete, conventionally formatted MAC addresses."""
    if not isinstance(value, str):
        return None
    if not re.fullmatch(
        r"[a-fA-F0-9]{12}|(?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}|(?:[a-fA-F0-9]{2}-){5}[a-fA-F0-9]{2}", value
    ):
        return None
    return value.replace(":", "").replace("-", "").upper()


def number(data: dict[str, Any], key: str, low: float = -10000, high: float = 10000) -> float | None:
    """Reject absent, boolean, nonfinite and implausible numeric values."""
    value = data.get(key)
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except TypeError, ValueError:
        return None
    return result if math.isfinite(result) and low <= result <= high else None


def code(data: dict[str, Any], key: str) -> str | None:
    """Normalize the vendor's short hexadecimal status strings."""
    value = data.get(key)
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{1,2}", value):
        return value.upper().zfill(2)
    if type(value) is int and 0 <= value <= 255:
        return f"{value:02X}"
    return None


def powered(data: dict[str, Any]) -> bool | None:
    """Status 02 is enabled/reached, not shutdown or measured compressor activity."""
    value = code(data, "powerStatus")
    return value != "00" if value in {"00", "01", "02"} else None


def faults(data: dict[str, Any]) -> list[str] | None:
    value = code(data, "errCode")
    if value is None:
        return None
    mask = int(value, 16)
    return [label for bit, label in FAULT_BITS.items() if mask & (1 << bit)]


def vendor_datetime(data: dict[str, Any]) -> datetime | None:
    """Preserve explicit timezone information; never guess the vendor's zone."""
    value = data.get("updateTimeStr")
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?", value
    ):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
