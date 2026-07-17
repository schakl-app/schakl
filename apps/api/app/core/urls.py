"""Shared guard against dangerous URL schemes in tenant-supplied links (security audit web-XSS).

Several fields store a URL that the web app later renders into an ``href`` (company website, task
links, …). Svelte escapes attribute *values* but does not strip dangerous *schemes*, so a stored
``javascript:``/``data:``/``vbscript:`` URL executes on click — a stored-XSS that fires for whoever
opens the record, including an admin/owner (privilege escalation). Reject those schemes at the API
boundary, the one place every client (web, MCP, public API) shares.

Denylist, not allowlist, on purpose: a bare host (``example.com``) and ``mailto:``/``tel:`` are
legitimate values we must not break; only the script-executing schemes are refused.
"""

from __future__ import annotations

import re

from app.errors import AppError

# Match the browser's own leniency: it ignores ASCII control chars / whitespace inside a scheme
# (``java\tscript:`` runs), so strip them before testing the prefix.
_CONTROL_WS = re.compile(r"[\x00-\x20]+")
_DANGEROUS_SCHEMES = ("javascript:", "data:", "vbscript:", "file:")


def reject_dangerous_url(value: str | None, *, field: str) -> str | None:
    """Return ``value`` unchanged, or raise 422 if it carries a script-executing URL scheme."""
    if value is None:
        return value
    compact = _CONTROL_WS.sub("", value).lower()
    if compact.startswith(_DANGEROUS_SCHEMES):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={field: "errors.validation"},
        )
    return value
