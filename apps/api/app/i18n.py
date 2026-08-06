"""Server-side i18n (CLAUDE.md §8).

Loads the **same** flat, namespaced catalogs the web app uses (``messages/<locale>.json``),
so keys never drift between client and server. The REST error envelope returns i18n *keys*
(the client translates); this module is for server-rendered text — emails, notifications,
PDFs (P2+). Locale resolves user → org default → ``nl``.

Interpolation here is simple ``{param}`` substitution; rich ICU plural/select formatting lives
on the web side (Paraglide). Server strings should avoid ICU plurals.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from app.config import settings

_PARAM_RE = re.compile(r"\{(\w+)\}")


@lru_cache
def _catalogs() -> dict[str, dict[str, str]]:
    catalogs: dict[str, dict[str, str]] = {}
    for path in sorted(settings.messages_dir.glob("*.json")):
        locale = path.stem
        data = json.loads(path.read_text(encoding="utf-8"))
        catalogs[locale] = {k: v for k, v in data.items() if not k.startswith("$")}
    return catalogs


def available_locales() -> list[str]:
    return sorted(_catalogs().keys())


def resolve_locale(user_locale: str | None = None, org_default: str | None = None) -> str:
    catalogs = _catalogs()
    for candidate in (user_locale, org_default, settings.default_locale, "nl"):
        if candidate and candidate in catalogs:
            return candidate
    return next(iter(catalogs), settings.default_locale)


def translations(key: str) -> list[str]:
    """Every locale's text for ``key``, deduplicated, in catalog order.

    The inverse of :func:`translate`, and it exists for *recognition* rather than display: the
    import wizard has to match a header or a cell a human typed against what this product calls
    that thing — and the product calls it something different per locale. Reading the catalogs
    means a column or an option is recognised in every language the instance ships the moment
    §8's "add the key to every locale in the same change" rule is followed, with no second,
    hand-maintained list of spellings to drift out of sync.
    """
    found: dict[str, None] = {}
    for catalog in _catalogs().values():
        text = catalog.get(key)
        if text:
            found.setdefault(text, None)
    return list(found)


def translate(key: str, locale: str | None = None, /, **params: object) -> str:
    """Translate ``key`` in ``locale`` (fallback: default locale, then the key itself)."""
    catalogs = _catalogs()
    loc = resolve_locale(locale)
    template = catalogs.get(loc, {}).get(key)
    if template is None:
        template = catalogs.get(settings.default_locale, {}).get(key, key)
    if params:
        return _PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), template)
    return template
