"""Strict JSON parser regression tests."""

import pytest

from app.strict_json import loads_strict_json


@pytest.mark.parametrize(
    "payload",
    [
        '{"completed": false, "completed": true}',
        '{"outer": {"mode": "deny", "mode": "allow"}}',
    ],
)
def test_rejects_duplicate_keys_at_any_depth(payload):
    with pytest.raises(ValueError, match="duplicate JSON key"):
        loads_strict_json(payload)


@pytest.mark.parametrize(
    "payload", ['{"score": NaN}', '{"score": Infinity}', '{"score": -Infinity}']
)
def test_rejects_non_finite_json_constants(payload):
    with pytest.raises(ValueError, match="non-finite JSON constant"):
        loads_strict_json(payload)


def test_rejects_numeric_overflow_that_decodes_to_infinity():
    with pytest.raises(ValueError, match="non-finite JSON number"):
        loads_strict_json('{"score": 1e999}')


def test_accepts_valid_finite_json_and_preserves_nested_values():
    assert loads_strict_json('{"count": 2, "score": 0.25, "nested": {"ok": true}}') == {
        "count": 2,
        "score": 0.25,
        "nested": {"ok": True},
    }
