"""Strict JSON decoding for evidence and other untrusted local inputs.

Reject duplicate object keys and non-finite numbers instead of inheriting
Python's permissive last-key-wins / NaN parsing behavior.
"""

from __future__ import annotations

import json
import math
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def loads_strict_json(data: str | bytes | bytearray) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite numbers."""
    return json.loads(
        data,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )
