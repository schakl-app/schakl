"""Tenant-configurable number formats — the shared primitive behind every counted record.

Invoices and quotes (issue #207) and company client numbers (klantnummer) all answer the same
question: "what does *our* numbering look like?" That is bookkeeping convention which differs
per agency, so it is tenant configuration, never hardcoded. Three tokens — ``{year}``,
``{yy}``, and ``{seq}`` (or ``{seq:N}`` zero-padded to N digits) — and literal text around
them. Exactly one ``{seq}``: a template without a counter cannot be unique, and one with two
counters has no single sequence to increment.

These are **pure functions**. Allocation — locking the owning settings row, walking past a
number a rewound sequence has already handed out, resetting on the org-local year — belongs to
whichever service owns the counter (``InvoicingSettingsService.allocate_number``,
``CompanySettingsService.allocate_client_number``), because the row to lock differs.
The web mirrors these two functions in ``apps/web/src/lib/core/numbering.ts`` for the live
preview; the pair must stay in step.
"""

from __future__ import annotations

import re

_SEQ_RE = re.compile(r"\{seq(?::(\d{1,2}))?\}")
_KNOWN_TOKEN_RE = re.compile(r"\{(?:year|yy|seq(?::\d{1,2})?)\}")
_ANY_TOKEN_RE = re.compile(r"\{[^{}]*\}")


def format_valid(fmt: str) -> bool:
    """A usable format: non-empty, exactly one ``{seq}`` token, no unknown ``{…}`` tokens."""
    if not fmt or not fmt.strip():
        return False
    if len(_SEQ_RE.findall(fmt)) != 1:
        return False
    return all(_KNOWN_TOKEN_RE.fullmatch(token) for token in _ANY_TOKEN_RE.findall(fmt))


def format_number(fmt: str, *, year: int, seq: int) -> str:
    def _seq(match: re.Match[str]) -> str:
        pad = match.group(1)
        return str(seq).zfill(int(pad)) if pad else str(seq)

    out = _SEQ_RE.sub(_seq, fmt)
    out = out.replace("{year}", str(year)).replace("{yy}", f"{year % 100:02d}")
    return out
