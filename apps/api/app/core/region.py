"""Where the organisation is — the country a value is read *in* when the value doesn't say.

Sibling of ``app/core/timezone.py``: same shape (one column on ``org_settings``, one resolver),
same reason to exist. A timezone answers "when is today here"; this answers "whose country code
does a bare ``0612345678`` belong to".

Only consulted when the data is genuinely ambiguous. A phone that already carries ``+31`` is
international and this is never asked; a company row that names its own ``country`` uses that.
So the org default is the *last* fallback, never an override — which is what keeps a Belgian
client in a Dutch tenant's import Belgian.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: Used when an org somehow has no settings row yet (first-run, mid-setup). The instance ships
#: Dutch-first (CLAUDE.md §1), and a wrong guess here can only affect a national number that
#: carried no country of its own anyway.
DEFAULT_COUNTRY = "NL"


def is_valid_country(code: str | None) -> bool:
    """True for something shaped like an ISO 3166-1 alpha-2 code.

    Shape only, deliberately: the column is tenant data and the consumers
    (``phonenumbers``, display) each reject an unknown-but-well-formed code far better than a
    hardcoded country list here would, and that list would need maintaining.
    """
    return bool(code) and len(code) == 2 and code.isalpha()


async def org_default_country(session: AsyncSession, org_id) -> str:
    """The org's configured country, uppercased, or the instance default.

    Reads ``org_settings`` on the caller's (RLS-bound) session, like ``org_timezone_name``.
    Call it lazily — only once a value has actually turned out to be ambiguous — so an ordinary
    write never pays for a lookup it doesn't use (docs/PERFORMANCE.md).
    """
    from app.core.models import OrgSettings

    code = await session.scalar(
        select(OrgSettings.default_country).where(OrgSettings.org_id == org_id)
    )
    code = (code or "").strip().upper()
    return code if is_valid_country(code) else DEFAULT_COUNTRY
