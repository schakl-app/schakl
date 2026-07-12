"""Declarative condition trees (issue #27).

A rule's ``conditions`` is a small JSONB predicate tree evaluated against the trigger event's
payload merged over a snapshot of the entity's own columns:

    {}                                          -> always matches
    {"field": "status", "op": "eq", "value": "done"}
    {"all": [<node>, ...]}                      -> every child matches (AND)
    {"any": [<node>, ...]}                      -> at least one child matches (OR)

Ops: ``eq`` ``ne`` ``in`` ``contains`` ``gt`` ``lt``. **No user-supplied code, ever** — the
issue is explicit that an eval box in a self-hosted admin screen is an RCE hole with extra
steps. The tree is validated on write (shape, ops, depth, node count) so a stored rule can
always be evaluated, and evaluation itself never raises: an incomparable pair is simply
``False``.
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError

#: Bounds on the tree, enforced at write time. Deep recursion on request-supplied JSON is a
#: stack-abuse vector; nobody authors a 6-level nested rule in good faith.
MAX_TREE_DEPTH = 5
MAX_TREE_NODES = 50

OPS: tuple[str, ...] = ("eq", "ne", "in", "contains", "gt", "lt")


def _invalid() -> AppError:
    return AppError(
        "validation",
        "errors.validation",
        status_code=422,
        fields={"conditions": "errors.automation_invalid_conditions"},
    )


def validate(tree: Any) -> None:
    """Reject anything that is not a well-formed, bounded predicate tree."""
    count = _validate_node(tree, depth=0)
    if count > MAX_TREE_NODES:
        raise _invalid()


def _validate_node(node: Any, *, depth: int) -> int:
    if depth > MAX_TREE_DEPTH:
        raise _invalid()
    if not isinstance(node, dict):
        raise _invalid()
    if not node:
        return 1  # {} = match-all
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        if set(node.keys()) != {key} or not isinstance(node[key], list):
            raise _invalid()
        return 1 + sum(_validate_node(child, depth=depth + 1) for child in node[key])
    if set(node.keys()) != {"field", "op", "value"}:
        raise _invalid()
    if not isinstance(node["field"], str) or not node["field"]:
        raise _invalid()
    if node["op"] not in OPS:
        raise _invalid()
    value = node["value"]
    if isinstance(value, dict):
        raise _invalid()
    if isinstance(value, list) and any(isinstance(item, dict | list) for item in value):
        raise _invalid()
    return 1


def evaluate(tree: Any, data: dict[str, Any]) -> bool:
    """Does ``data`` satisfy ``tree``? Never raises — malformed leftovers evaluate ``False``."""
    if not isinstance(tree, dict):
        return False
    if not tree:
        return True
    if "all" in tree:
        children = tree["all"]
        return isinstance(children, list) and all(evaluate(c, data) for c in children)
    if "any" in tree:
        children = tree["any"]
        return isinstance(children, list) and any(evaluate(c, data) for c in children)
    field, op, expected = tree.get("field"), tree.get("op"), tree.get("value")
    if not isinstance(field, str) or op not in OPS:
        return False
    actual = data.get(field)
    return _compare(op, actual, expected)


def _numeric(value: Any) -> float | None:
    """A comparable number, when the value honestly is one ("5" from a form counts)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _loose_eq(a: Any, b: Any) -> bool:
    if a == b:
        return True
    left, right = _numeric(a), _numeric(b)
    return left is not None and right is not None and left == right


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return _loose_eq(actual, expected)
    if op == "ne":
        return not _loose_eq(actual, expected)
    if op == "in":
        if not isinstance(expected, list):
            return False
        return any(_loose_eq(actual, item) for item in expected)
    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return expected.lower() in actual.lower()
        if isinstance(actual, list):
            return any(_loose_eq(item, expected) for item in actual)
        return False
    if op in ("gt", "lt"):
        left, right = _numeric(actual), _numeric(expected)
        if left is None or right is None:
            # Honest string comparison only between two real strings (dates in ISO order).
            if isinstance(actual, str) and isinstance(expected, str):
                return actual > expected if op == "gt" else actual < expected
            return False
        return left > right if op == "gt" else left < right
    return False
