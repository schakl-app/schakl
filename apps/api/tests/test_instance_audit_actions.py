"""The instance auditlog's vocabulary is enumerable, and every word of it is translated (#359).

The console printed the raw event key in a monospace font — ``impersonate.start`` on the one
screen an org owner reads to find out who signed in as whom — because the vocabulary was thirty
odd string literals spread over ten modules and nothing could enumerate it. :data:`ACTIONS` is
that enumeration; these tests are what keeps it honest in both directions.

They are source-level on purpose. Every other route to the same guarantee (a runtime registry, a
``record()`` that raises) either fires only on the code path that already ran or turns a missing
label into a failed *mutation*, which is a far worse outcome than an untranslated row.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import settings
from app.core.instance.audit import ACTIONS

_API_ROOT = Path(__file__).resolve().parents[1] / "app"

#: ``audit.record(..., action="org.create", ...)`` — the literal, wherever it is written.
_RECORD_CALL = re.compile(r"audit\.record\((?P<args>.*?)\)", re.DOTALL)
_ACTION_KWARG = re.compile(r'action="(?P<action>[a-z0-9_.]+)"')


def _recorded_actions() -> set[str]:
    found: set[str] = set()
    for path in _API_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "audit.record(" not in source:
            continue
        # The org-scoped role trail (`app.core.permissions.audit`) is a different table with a
        # different vocabulary, reached through the same `audit.record` spelling — so the
        # discriminator is which module the file imported, not where the file lives.
        if "from app.core.permissions import audit" in source:
            continue
        for call in _RECORD_CALL.finditer(source):
            match = _ACTION_KWARG.search(call.group("args"))
            if match:
                found.add(match.group("action"))
    return found


def _catalog(locale: str) -> dict[str, str]:
    path = settings.messages_dir / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_recorded_action_is_declared() -> None:
    """A literal nobody added to ``ACTIONS`` would render untranslated on the console."""
    undeclared = _recorded_actions() - ACTIONS
    assert not undeclared, f"instance audit actions missing from ACTIONS: {sorted(undeclared)}"


def test_the_scan_actually_found_the_trail() -> None:
    """A regex that silently matches nothing would make the test above pass forever."""
    recorded = _recorded_actions()
    assert len(recorded) >= 20, f"only found {len(recorded)} audit actions — did the scan break?"
    assert "impersonate.start" in recorded


@pytest.mark.parametrize("locale", ["en", "nl"])
def test_every_declared_action_has_a_label(locale: str) -> None:
    catalog = _catalog(locale)
    missing = [a for a in sorted(ACTIONS) if f"instance.audit.action.{a}" not in catalog]
    assert not missing, f"{locale}.json is missing instance.audit.action.* for: {missing}"


@pytest.mark.parametrize("locale", ["en", "nl"])
def test_the_auditlog_table_has_column_headers(locale: str) -> None:
    catalog = _catalog(locale)
    for key in ("col_when", "col_action", "col_org", "col_actor"):
        assert f"instance.audit.{key}" in catalog
