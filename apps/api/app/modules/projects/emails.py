"""The project budget alert mail (nightly watch, `budget_watch.py`).

An internal mail to the people on the project, sent when the burn crosses the org's warn
threshold and again when it goes over — the moments a budget can still be managed. The body
carries the evidence, not just the verdict: a burn bar with the threshold marked on it, the
numbers behind the percentage, and the period they cover, so the reader does not have to open
the project to learn whether "82%" is about this month or about the whole engagement.

Composition is a pure function on purpose (the `domain_alert.py` shape): the sweep's side
effects — the time read, the transport — are all outside it, so the body is testable without
any of them. Tier 1 (`docs/EMAIL.md` step 0): an internal operational mail, not one a tenant
rewords; the branded chrome still wraps it at the send seam.
"""

from __future__ import annotations

import html as html_lib

from app.core.email.branding import FONT_STACK, button_html
from app.core.email.senders import OutgoingEmail
from app.i18n import translate

_MUTED = "#6b7280"
_TEXT = "#1f2937"
_TRACK = "#e5e7eb"
_MARKER = "#111827"
#: Amber while the budget can still be managed, red once it is gone.
_WARN = "#f59e0b"
_OVER = "#dc2626"


def _fmt_hours(value: float, locale: str) -> str:
    """One decimal, dropped when whole; decimal comma for every locale but English."""
    rounded = round(value, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    out = f"{rounded:.1f}"
    return out if locale.startswith("en") else out.replace(".", ",")


def _p(text: str, *, muted: bool = False, bold: bool = False) -> str:
    color = _MUTED if muted else _TEXT
    weight = "700" if bold else "400"
    size = "13px" if muted else "15px"
    return (
        f'<p style="margin:0 0 12px 0;font-family:{FONT_STACK};font-size:{size};'
        f'font-weight:{weight};color:{color};">{html_lib.escape(text)}</p>'
    )


def _burn_bar(percent: int, threshold: int, fill_color: str) -> str:
    """The burn as a bar with the warn threshold marked on it.

    Segment widths are percent-attribute table cells (tables and inline styles only,
    docs/EMAIL.md). The fill caps at 100 — the *number* beside it says 112%, the bar does not
    pretend to have room for it — and the dark marker sits at the threshold, inside the fill
    once it has been crossed.
    """
    fill = max(0, min(percent, 100))
    mark = max(1, min(threshold, 98))
    if fill >= mark:
        beyond = max(fill - mark - 2, 0)
        segments = [
            (mark, fill_color),
            (2, _MARKER),
            (beyond, fill_color),
            (100 - mark - 2 - beyond, _TRACK),
        ]
    else:
        segments = [(fill, fill_color), (mark - fill, _TRACK), (2, _MARKER), (98 - mark, _TRACK)]
    cells = "".join(
        f'<td width="{width}%" height="14" '
        f'style="background-color:{color};font-size:0;line-height:0;">&nbsp;</td>'
        for width, color in segments
        if width > 0
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="width:100%;border-collapse:collapse;margin:4px 0 16px 0;">'
        f"<tr>{cells}</tr></table>"
    )


def _facts_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;font-family:{FONT_STACK};font-size:13px;'
        f'color:{_MUTED};white-space:nowrap;">{html_lib.escape(label)}</td>'
        f'<td style="padding:6px 0;font-family:{FONT_STACK};font-size:13px;'
        f'color:{_TEXT};">{html_lib.escape(value)}</td></tr>'
        for label, value in rows
    )
    return (
        '<table cellpadding="0" cellspacing="0" border="0"'
        f' style="border-collapse:collapse;margin:0 0 12px 0;">{body}</table>'
    )


def compose_budget_alert(
    *,
    project_name: str,
    project_url: str,
    company_name: str | None,
    level: str,
    percent: int,
    spent_hours: float,
    budget_hours: float,
    budget_period: str,
    threshold: int,
    locale: str,
    primary_color: str,
) -> OutgoingEmail:
    """The alert body for one project. ``to`` is left blank — the caller fills it per recipient.

    ``level`` is ``warn`` (the threshold is crossed, the budget can still be managed) or
    ``over`` (it is spent); both carry the same evidence, because both are answered by looking
    at the same hours.
    """
    intro = translate(
        f"projects.email.budget_{level}_intro", locale, project=project_name, percent=percent
    )
    spent_value = translate(
        "projects.email.budget_spent_value",
        locale,
        spent=_fmt_hours(spent_hours, locale),
        budget=_fmt_hours(budget_hours, locale),
    )
    remaining_value = translate(
        "projects.email.budget_remaining_value",
        locale,
        hours=_fmt_hours(budget_hours - spent_hours, locale),
    )
    period_value = translate(f"projects.budget_period.{budget_period}", locale)
    bar_alt = translate(
        "projects.email.budget_bar_alt", locale, percent=percent, threshold=threshold
    )
    threshold_note = translate(
        "projects.email.budget_threshold_note", locale, threshold=threshold
    )
    cta = translate("projects.email.budget_cta", locale)

    facts = [
        (translate("projects.email.budget_spent_label", locale), spent_value),
        (translate("projects.email.budget_remaining_label", locale), remaining_value),
        (translate("projects.field.budget_period", locale), period_value),
    ]
    if company_name:
        facts.insert(0, (translate("projects.field.company", locale), company_name))

    text_lines = [intro, "", bar_alt]
    text_lines += [f"{label}: {value}" for label, value in facts]
    text_lines += ["", threshold_note, "", f"{cta}: {project_url}"]

    fill_color = _OVER if level == "over" else _WARN
    percent_line = (
        f'<p style="margin:0;font-family:{FONT_STACK};font-size:24px;font-weight:700;'
        f'color:{fill_color};">{percent}%</p>'
    )
    html_parts = [
        _p(intro),
        percent_line,
        _burn_bar(percent, threshold, fill_color),
        _facts_table(facts),
        button_html(cta, project_url, primary_color),
        _p(threshold_note, muted=True),
    ]

    return OutgoingEmail(
        to="",
        subject=translate(
            f"projects.email.budget_{level}_subject", locale, project=project_name
        ),
        text="\n".join(text_lines),
        html="\n".join(html_parts),
    )
