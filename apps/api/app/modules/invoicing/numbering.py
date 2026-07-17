"""Document numbering (issue #207): tenant-configurable format, race-safe allocation.

A number is assigned when a document is **issued**, never while it drafts — so the sequence
counts documents that legally exist, and two admins issuing at once cannot collide: the
allocator locks the org's ``invoicing_settings`` row (``SELECT … FOR UPDATE``), formats,
increments, and the transaction the issue rides commits both together.

The format is a template with three tokens — ``{year}``, ``{yy}``, ``{seq}`` (or ``{seq:N}``
zero-padded to N digits) — because "what does a factuurnummer look like" is bookkeeping
convention that differs per agency, not something to hardcode. With ``number_reset_yearly``
the sequence restarts at 1 when the org-local year rolls over (2026-0001 style).
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
