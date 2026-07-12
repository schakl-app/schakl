"""Unit tests for the declarative condition tree (issue #27) — pure logic, no database."""

from __future__ import annotations

import pytest

from app.errors import AppError
from app.modules.automation.conditions import MAX_TREE_DEPTH, evaluate, validate


def _leaf(field: str, op: str, value) -> dict:
    return {"field": field, "op": op, "value": value}


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #
def test_empty_tree_matches_everything() -> None:
    assert evaluate({}, {}) is True
    assert evaluate({}, {"status": "done"}) is True


def test_eq_ne() -> None:
    data = {"status": "done", "count": 3}
    assert evaluate(_leaf("status", "eq", "done"), data)
    assert not evaluate(_leaf("status", "eq", "open"), data)
    assert evaluate(_leaf("status", "ne", "open"), data)
    # Numeric looseness: a form posts "3", the snapshot holds 3.
    assert evaluate(_leaf("count", "eq", "3"), data)


def test_in_and_contains() -> None:
    data = {"status": "review", "labels": ["urgent", "client"], "title": "Fix the Homepage"}
    assert evaluate(_leaf("status", "in", ["review", "done"]), data)
    assert not evaluate(_leaf("status", "in", ["open"]), data)
    assert not evaluate(_leaf("status", "in", "review"), data)  # non-list = no match
    assert evaluate(_leaf("labels", "contains", "urgent"), data)
    assert evaluate(_leaf("title", "contains", "homepage"), data)  # case-insensitive
    assert not evaluate(_leaf("title", "contains", "footer"), data)


def test_gt_lt() -> None:
    data = {"hours": 7.5, "name": "beta", "due_date": "2026-07-12"}
    assert evaluate(_leaf("hours", "gt", 5), data)
    assert evaluate(_leaf("hours", "lt", "10"), data)
    assert not evaluate(_leaf("hours", "gt", 7.5), data)
    # Two real strings compare lexically (ISO dates order correctly).
    assert evaluate(_leaf("due_date", "lt", "2026-08-01"), data)
    # Incomparable pairs are simply False, never an exception.
    assert not evaluate(_leaf("name", "gt", 5), data)
    assert not evaluate(_leaf("missing", "gt", 5), data)


def test_all_any_nesting() -> None:
    data = {"status": "done", "priority": "high"}
    tree = {
        "all": [
            _leaf("status", "eq", "done"),
            {"any": [_leaf("priority", "eq", "high"), _leaf("priority", "eq", "urgent")]},
        ]
    }
    assert evaluate(tree, data)
    assert not evaluate(tree, {"status": "done", "priority": "low"})
    assert evaluate({"any": []}, data) is False
    assert evaluate({"all": []}, data) is True


def test_evaluate_never_raises_on_junk() -> None:
    assert evaluate("nonsense", {}) is False  # type: ignore[arg-type]
    assert evaluate({"field": "x"}, {}) is False
    assert evaluate({"field": "x", "op": "explode", "value": 1}, {}) is False


# --------------------------------------------------------------------------- #
# validate — write-time gate
# --------------------------------------------------------------------------- #
def test_validate_accepts_well_formed_trees() -> None:
    validate({})
    validate(_leaf("status", "eq", "done"))
    validate({"all": [_leaf("a", "in", ["x", 1]), {"any": [_leaf("b", "lt", 2)]}]})


@pytest.mark.parametrize(
    "tree",
    [
        "not a dict",
        {"field": "x", "op": "eval", "value": "os.system"},  # unknown op
        {"field": "", "op": "eq", "value": 1},  # empty field
        {"field": "x", "op": "eq"},  # missing value
        {"field": "x", "op": "eq", "value": 1, "extra": True},  # stray key
        {"all": [_leaf("a", "eq", 1)], "any": []},  # both combinators
        {"all": "nope"},
        {"field": "x", "op": "eq", "value": {"nested": "dict"}},  # value must be scalar/list
    ],
)
def test_validate_rejects_malformed(tree) -> None:
    with pytest.raises(AppError):
        validate(tree)


def test_validate_depth_cap() -> None:
    tree: dict = _leaf("a", "eq", 1)
    for _ in range(MAX_TREE_DEPTH + 1):
        tree = {"all": [tree]}
    with pytest.raises(AppError):
        validate(tree)


def test_validate_node_cap() -> None:
    with pytest.raises(AppError):
        validate({"all": [_leaf(f"f{i}", "eq", i) for i in range(60)]})
