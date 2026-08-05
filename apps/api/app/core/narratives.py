"""The seam that lets a live dashboard borrow the words from a written report (issue #300).

The insight the n8n workflow was built on is that a client cannot read a GA4 table but *can*
read "we zien dat het organisch verkeer meebeweegt met…". The workflow put that sentence in a
monthly PDF and nowhere else, so for twenty-nine days of the month the client's dashboard was
back to being a table.

This is the other half: the marketing panel, tab and portal widget show the **latest published
report's** paragraph for the section they are drawing, dated, beside the numbers. It costs
nothing — the text is already stored — and it is honest, because it says which month it is
describing rather than pretending to narrate today.

It is a **seam** rather than an import because CLAUDE.md §6 forbids `marketing` importing
`reporting`, and rightly: the borrower must keep working with the lender uninstalled or
unlicensed. No provider registered — reporting disabled, or an instance that never bought it —
means no narrative, and every screen renders exactly as it did before. The registration shape
is ``app/core/portal.py``'s, applied to prose.

**Authorization stays the lender's.** The provider resolves through the reporting module's own
service, so the portal repository decides what a client-facing login may read: their own
company's *published, client-facing* reports and nothing else. A borrower that reached into
``reports`` itself would be re-implementing that rule, which is the mistake
``app/core/directory.py`` exists to prevent one layer down.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext


@dataclass(frozen=True)
class CompanyNarrative:
    """The most recent published prose about one client, and which period it describes."""

    report_id: uuid.UUID
    #: The period in words, in the *report's* language — "juli 2026". Shown beside the text so
    #: a reader is never told a paragraph about July is about today.
    period_label: str
    summary: str = ""
    #: ``{section_key: text}`` — keyed by the same ``ReportSectionSpec.key`` the section that
    #: produced the numbers is registered under, which is what lets a panel match them up.
    sections: dict[str, str] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "report_id": str(self.report_id),
            "period_label": self.period_label,
            "summary": self.summary,
            "sections": dict(self.sections),
        }


NarrativeProvider = Callable[
    ["RequestContext", uuid.UUID], Awaitable[CompanyNarrative | None]
]

_provider: NarrativeProvider | None = None


def register_narrative_provider(provider: NarrativeProvider) -> None:
    """Called by the reporting module's package ``__init__`` — the event-bus shape."""
    global _provider  # noqa: PLW0603 — one process-wide registration, like the scope resolver
    _provider = provider


async def latest_narrative(
    ctx: RequestContext, company_id: uuid.UUID
) -> CompanyNarrative | None:
    """The latest published narrative for this client, or ``None``.

    ``None`` is the ordinary answer, not an error: no reporting module, no licence, no report
    yet, or a client whose first report is still being written. Every caller treats it as
    "draw the numbers on their own", which is what they did before this existed.
    """
    if _provider is None:
        return None
    return await _provider(ctx, company_id)
