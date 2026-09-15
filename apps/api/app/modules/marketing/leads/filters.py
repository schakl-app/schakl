"""GA4 Data API ``FilterExpression`` builders — the whole filter logic a leads dashboard needs.

The Data API's expression tree (``andGroup`` / ``orGroup`` / ``notExpression`` over a
``filter``) is richer than the three-operator string grammar the ``google_analytics``
integration exposes on its query string, and this module needs all of it: a phone-and-e-mail
scorecard is an OR of several event matchers, a request scorecard with a user's service filter
is an AND of that OR and an ``inListFilter``, and "everything but applications" is a NOT.

Written against the v1beta reference shape: ``stringFilter.matchType`` is one of ``EXACT``,
``BEGINS_WITH``, ``ENDS_WITH``, ``CONTAINS``, ``FULL_REGEXP``, ``PARTIAL_REGEXP``;
``inListFilter`` takes ``values`` and ``caseSensitive``; groups take ``expressions``.
"""

from __future__ import annotations

from typing import Any

from app.modules.marketing.leads.profile import EventMatcher

_MATCH_TYPES = {
    "exact": "EXACT",
    "begins_with": "BEGINS_WITH",
    "contains": "CONTAINS",
    "regex": "FULL_REGEXP",
}

FilterExpression = dict[str, Any]


def string_filter(field: str, value: str, match: str = "exact") -> FilterExpression:
    return {
        "filter": {
            "fieldName": field,
            "stringFilter": {
                "matchType": _MATCH_TYPES[match],
                "value": value,
                "caseSensitive": False,
            },
        }
    }


def in_list(field: str, values: list[str]) -> FilterExpression:
    """``field IN (values)`` — one expression however many values, which is what keeps a
    request filter carrying twelve exact event names from becoming twelve expressions."""
    return {
        "filter": {
            "fieldName": field,
            "inListFilter": {"values": list(values), "caseSensitive": False},
        }
    }


def any_of(expressions: list[FilterExpression]) -> FilterExpression | None:
    """OR. One expression is returned bare; none is ``None`` (no filter at all)."""
    parts = [e for e in expressions if e]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"orGroup": {"expressions": parts}}


def all_of(expressions: list[FilterExpression | None]) -> FilterExpression | None:
    """AND, with the same one/none rules as :func:`any_of`."""
    parts = [e for e in expressions if e]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"andGroup": {"expressions": parts}}


def not_(expression: FilterExpression) -> FilterExpression:
    return {"notExpression": expression}


def event_filter(matchers: list[EventMatcher], field: str = "eventName") -> FilterExpression | None:
    """``eventName`` matches any of ``matchers``: the exact ones fold into one ``inListFilter``,
    the rest become string filters, all OR-ed. ``None`` when there is nothing to match, so a
    caller asking for a role the profile does not carry gets *no filter* back and must treat
    that as "not measured" rather than as "everything" — see ``widgets.py``."""
    if not matchers:
        return None
    exact = [m.value for m in matchers if m.match == "exact"]
    others = [string_filter(field, m.value, m.match) for m in matchers if m.match != "exact"]
    parts: list[FilterExpression] = []
    if exact:
        parts.append(in_list(field, exact) if len(exact) > 1 else string_filter(field, exact[0]))
    parts.extend(others)
    return any_of(parts)


def matches(matchers: list[EventMatcher], event_name: str) -> bool:
    """The same predicate, applied locally — used to sort a report's rows back into roles when
    one report carries several roles at once (the events report), and by the tests that pin the
    server-side filter against the local reading of it."""
    import re

    name = event_name.lower()
    for m in matchers:
        value = m.value.lower()
        if m.match == "exact" and name == value:
            return True
        if m.match == "begins_with" and name.startswith(value):
            return True
        if m.match == "contains" and value in name:
            return True
        if m.match == "regex" and re.fullmatch(m.value, event_name, flags=re.IGNORECASE):
            return True
    return False
