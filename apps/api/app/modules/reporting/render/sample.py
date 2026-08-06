"""A report-shaped stand-in, for previewing a design that has no report to draw yet.

The editor's live preview renders the tenant's **own** most recent report wherever there is
one — that is the honest preview, and the whole reason the renderer is shared with the print
path. This is the other case: a tenant configuring reporting before the first run, who would
otherwise be authoring Jinja against a blank frame.

Two rules keep it from becoming a lie.

**The section keys are the registry's**, so the headings say what the tenant's own document
will say and a section a later release adds appears here without this file changing. Only the
numbers are invented.

**It is never persisted.** The row is constructed and handed to the renderer; nothing adds it
to the session, so it cannot collide with the ``(org, company, audience, period)`` uniqueness
that makes a real re-run update a document instead of mailing a second copy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.config import settings as app_settings
from app.modules.reporting.models import Report, ReportStatus
from app.registry import registry

#: Enough sections to show what a design does *between* sections — the alternating band only
#: exists as a rhythm, and one section cannot have one.
_MAX_SECTIONS = 5

_LOREM = (
    "Dit is voorbeeldtekst. De echte alinea's worden per klant geschreven en zijn te "
    "bewerken voordat het rapport de deur uit gaat."
)
#: Not an i18n key on purpose: this is *sample content*, the same category as the invented
#: numbers beside it, and a catalog entry per fake channel name would suggest otherwise.
_CLIENT = "Voorbeeld B.V."


def _channels_section() -> dict[str, Any]:
    """A share-of-the-whole table: the shape the colour dots and the share bar both need."""
    return {
        "kind": "channels",
        "columns": ["sessions", "compare_sessions", "delta", "share"],
        "rows": [
            {"label": "Organic Search", "sessions": 4120, "compare_sessions": 3480,
             "delta": 18.4, "share": 52.1},
            {"label": "Direct", "sessions": 1890, "compare_sessions": 1955,
             "delta": -3.3, "share": 23.9},
            {"label": "Referral", "sessions": 980, "compare_sessions": 610,
             "delta": 60.7, "share": 12.4},
            {"label": "Organic Social", "sessions": 620, "compare_sessions": 705,
             "delta": -12.1, "share": 7.8},
            {"label": "Paid Search", "sessions": 300, "compare_sessions": 120,
             "delta": 150.0, "share": 3.8},
        ],
        "totals": {"sessions": 7910, "engagementRate": 0.61, "keyEvents": 148},
        "compare": {"sessions": 6870, "engagementRate": 0.58, "keyEvents": 121},
        "chart": {
            "type": "grouped",
            "labels": ["Organic Search", "Direct", "Referral", "Organic Social", "Paid Search"],
            "series": [
                {"key": "current", "values": [4120, 1890, 980, 620, 300]},
                {"key": "compare", "values": [3480, 1955, 610, 705, 120]},
            ],
        },
    }


def _share_section() -> dict[str, Any]:
    """A table under a share bar — where a row's dot is literally its segment."""
    rows = [
        {"label": "Google", "sessions": 3720, "compare_sessions": 3150, "delta": 18.1},
        {"label": "Bing", "sessions": 240, "compare_sessions": 190, "delta": 26.3},
        {"label": "DuckDuckGo", "sessions": 96, "compare_sessions": 88, "delta": 9.1},
        {"label": "Ecosia", "sessions": 64, "compare_sessions": 52, "delta": 23.1},
    ]
    return {
        "kind": "table",
        "columns": ["sessions", "compare_sessions", "delta"],
        "rows": rows,
        "totals": {},
        "compare": None,
        "chart": {
            "type": "share",
            "items": [{"label": row["label"], "value": row["sessions"]} for row in rows],
        },
    }


def _plain_section() -> dict[str, Any]:
    return {
        "kind": "table",
        "columns": ["clicks", "impressions", "ctr", "position"],
        "rows": [
            {"label": "/", "clicks": 410, "impressions": 9800, "ctr": 4.2, "position": 6.1},
            {"label": "/diensten", "clicks": 260, "impressions": 5100, "ctr": 5.1,
             "position": 4.8},
            {"label": "/contact", "clicks": 95, "impressions": 1400, "ctr": 6.8,
             "position": 3.2},
        ],
        "totals": {"clicks": 765, "impressions": 16300},
        "compare": {"clicks": 690, "impressions": 15100},
        "chart": None,
    }


#: Cycled over the registry's sections, so the sample shows every table shape the design draws
#: without this file having to know which module contributed which key.
_SHAPES = (_channels_section, _share_section, _plain_section)


def sample_report(audience: str, locale: str, today: date) -> Report:
    """A transient :class:`Report` with invented numbers under real section headings.

    ``today`` is the caller's — resolved once from the org's own zone (CLAUDE.md §8). A month
    label is a local-calendar fact even on a document nobody will ever send, and a module that
    reaches for ``date.today()`` is exactly the private clock that rule exists to stop.
    """
    from app.modules.reporting.generate import previous_month
    from app.modules.reporting.prompts import period_label

    keys = [
        spec.key
        for spec in registry.report_sections_for(audience, app_settings.enabled_modules)
    ][:_MAX_SECTIONS]
    sections = {key: _SHAPES[index % len(_SHAPES)]() for index, key in enumerate(keys)}
    start, end = previous_month(today)
    label = period_label(start, end, locale)
    return Report(
        company_name=_CLIENT,
        audience=audience,
        status=ReportStatus.DRAFT.value,
        locale=locale,
        title=label,
        period_start=start,
        period_end=end,
        data_snapshot={
            "order": keys,
            "sections": sections,
            "company": {"name": _CLIENT},
            "period": {"label": label},
            "compare": {"label": period_label(*previous_month(start), locale)},
        },
        narrative={
            "summary": _LOREM,
            **{key: _LOREM for key in keys},
            "actions": "Voorbeeldactie een\nVoorbeeldactie twee",
            "questions": "Voorbeeldvraag een",
        },
    )


__all__ = ["sample_report"]
