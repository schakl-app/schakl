"""The custom-domain alert mail (#291). Business-licensed — see this directory's LICENSE.

"Your domain is not working" is only half a message: the person reading it still has to go
find *which* record is wrong, what it holds now and what it should hold. So this mail carries
the evidence the sweep already gathered — the required records beside what DNS actually
answers, then each layer's own verdict — and does it in **exactly the words the settings
screen uses**: both render the same :class:`~app.core.domainflow.DomainCheck`, one from the
wizard's "check now", one from the unattended sweep, through the same
``settings.domain.*`` catalog keys. A mail that phrased the problem its own way would be a
second implementation of the diagnosis, and those drift.

Composition is a pure function on purpose: the sweep's side effects (Cloudflare, DNS, the
transport) are all outside it, so the body is testable without any of them.
"""

from __future__ import annotations

import html as html_lib
from collections.abc import Sequence
from datetime import datetime

from app.core import domainflow
from app.core.domainflow import DnsRecordCard, DomainCheck
from app.core.email.branding import FONT_STACK, button_html
from app.core.email.senders import OutgoingEmail
from app.core.hosts import slug_host
from app.core.models import Org
from app.i18n import translate

#: Where the recipient goes to fix it. The **slug** host, never the custom domain: this mail
#: exists because that domain may not be answering.
SETTINGS_PATH = "/settings/domain"

_MONO = "Consolas,Menlo,monospace"
_MUTED = "#6b7280"
_TEXT = "#1f2937"

#: Which check speaks for which record card. The mail shows one line per record, so the
#: card's expected value needs the observation that belongs to it.
_CHECK_FOR_PURPOSE = {"traffic": "dns_target", "ownership": "ownership"}


def _fmt_date(value: datetime | None) -> str:
    """European dd-mm-yyyy — the product's date language everywhere (docs/UX.md)."""
    return value.strftime("%d-%m-%Y") if value else ""


def _observed(check: DomainCheck | None, locale: str) -> str:
    if check is None or not check.observed:
        return translate("cloud.domain.email_not_found", locale)
    return check.observed


def _text_body(
    org: Org,
    *,
    kind: str,
    checks: Sequence[DomainCheck],
    records: Sequence[DnsRecordCard],
    locale: str,
    brand: str,
    settings_url: str,
    recipients: Sequence[str],
) -> str:
    by_key = {check.key: check for check in checks}
    expected_label = translate("settings.domain.expected", locale)
    observed_label = translate("settings.domain.observed", locale)
    lines: list[str] = [
        translate(
            f"cloud.domain.email_{kind}",
            locale,
            brand=brand,
            domain=org.custom_domain or "",
            date=_fmt_date(org.domain_cert_expires_at),
        )
    ]
    if records:
        lines += ["", translate("cloud.domain.email_records_heading", locale)]
        for card in records:
            check = by_key.get(_CHECK_FOR_PURPOSE.get(card.purpose, ""))
            lines += [
                f"  {card.type}  {card.name}",
                f"    {expected_label}: {card.value}",
                f"    {observed_label}: {_observed(check, locale)}",
            ]
        if domainflow.is_apex(org.custom_domain or ""):
            lines += ["", translate("settings.domain.routing.apex_note", locale)]
    problems = [check for check in checks if check.state != "ok"]
    if problems:
        lines += ["", translate("cloud.domain.email_findings_heading", locale)]
        for check in problems:
            label = translate(f"settings.domain.status.{check.key}", locale)
            lines.append(f"  {label}: {translate(check.message_key, locale)}")
    if org.domain_check_error:
        lines += ["", translate("cloud.domain.email_error", locale, error=org.domain_check_error)]
    lines += [
        "",
        f"{translate('cloud.domain.email_cta', locale)}: {settings_url}",
        translate("cloud.domain.email_recovery", locale, url=f"https://{slug_host(org)}"),
    ]
    if recipients:
        lines += [
            "",
            translate(
                "cloud.domain.email_recipients",
                locale,
                brand=brand,
                recipients=", ".join(recipients),
            ),
        ]
    return "\n".join(lines)


def _p(text: str, *, muted: bool = False, bold: bool = False) -> str:
    color = _MUTED if muted else _TEXT
    weight = "700" if bold else "400"
    size = "13px" if muted else "15px"
    return (
        f'<p style="margin:0 0 12px 0;font-family:{FONT_STACK};font-size:{size};'
        f'font-weight:{weight};color:{color};">{html_lib.escape(text)}</p>'
    )


def _records_table(
    records: Sequence[DnsRecordCard],
    by_key: dict[str, DomainCheck],
    locale: str,
) -> str:
    """One row per required record: type, name, the value it must hold — and, underneath in
    muted text, what the zone answers today. Tables and inline styles only (docs/EMAIL.md)."""
    head = "".join(
        f'<th align="left" style="padding:6px 8px;border-bottom:1px solid #e5e7eb;'
        f'font-family:{FONT_STACK};font-size:12px;color:{_MUTED};">'
        f"{html_lib.escape(translate(key, locale))}</th>"
        for key in (
            "settings.domain.record.type",
            "settings.domain.record.name",
            "settings.domain.record.value",
        )
    )
    rows: list[str] = []
    for card in records:
        cells = "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;'
            f'font-family:{_MONO};font-size:13px;color:{_TEXT};">{html_lib.escape(value)}</td>'
            for value in (card.type, card.name, card.value)
        )
        rows.append(f"<tr>{cells}</tr>")
        check = by_key.get(_CHECK_FOR_PURPOSE.get(card.purpose, ""))
        rows.append(
            f'<tr><td colspan="3" style="padding:0 8px 10px 8px;'
            f'font-family:{FONT_STACK};font-size:12px;color:{_MUTED};">'
            f"{html_lib.escape(translate('settings.domain.observed', locale))}: "
            f'<span style="font-family:{_MONO};">'
            f"{html_lib.escape(_observed(check, locale))}</span></td></tr>"
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="width:100%;border-collapse:collapse;margin:0 0 16px 0;">'
        f"<tr>{head}</tr>{''.join(rows)}</table>"
    )


def _html_body(
    org: Org,
    *,
    kind: str,
    checks: Sequence[DomainCheck],
    records: Sequence[DnsRecordCard],
    locale: str,
    brand: str,
    settings_url: str,
    primary_color: str,
    recipients: Sequence[str],
) -> str:
    by_key = {check.key: check for check in checks}
    parts: list[str] = [
        _p(
            translate(
                f"cloud.domain.email_{kind}",
                locale,
                brand=brand,
                domain=org.custom_domain or "",
                date=_fmt_date(org.domain_cert_expires_at),
            )
        )
    ]
    if records:
        parts.append(_p(translate("cloud.domain.email_records_heading", locale), bold=True))
        parts.append(_records_table(records, by_key, locale))
        if domainflow.is_apex(org.custom_domain or ""):
            parts.append(_p(translate("settings.domain.routing.apex_note", locale), muted=True))
    problems = [check for check in checks if check.state != "ok"]
    if problems:
        parts.append(_p(translate("cloud.domain.email_findings_heading", locale), bold=True))
        for check in problems:
            label = translate(f"settings.domain.status.{check.key}", locale)
            parts.append(_p(f"{label}: {translate(check.message_key, locale)}"))
    if org.domain_check_error:
        parts.append(
            _p(
                translate("cloud.domain.email_error", locale, error=org.domain_check_error),
                muted=True,
            )
        )
    parts.append(
        button_html(translate("cloud.domain.email_cta", locale), settings_url, primary_color)
    )
    parts.append(
        _p(
            translate("cloud.domain.email_recovery", locale, url=f"https://{slug_host(org)}"),
            muted=True,
        )
    )
    if recipients:
        parts.append(
            _p(
                translate(
                    "cloud.domain.email_recipients",
                    locale,
                    brand=brand,
                    recipients=", ".join(recipients),
                ),
                muted=True,
            )
        )
    return "\n".join(parts)


def compose_domain_alert(
    org: Org,
    *,
    kind: str,
    checks: Sequence[DomainCheck],
    locale: str,
    brand: str,
    primary_color: str,
    recipients: Sequence[str],
) -> OutgoingEmail:
    """The alert body for one org. ``to`` is left blank — the caller fills it per recipient.

    ``kind`` is ``unhealthy`` (the domain stopped serving) or ``expiry`` (a renewal that is
    evidently not happening); both carry the same evidence, because both are answered by
    fixing the same records.
    """
    records = domainflow.record_cards(org)
    settings_url = f"https://{slug_host(org)}{SETTINGS_PATH}"
    shared = {
        "kind": kind,
        "checks": checks,
        "records": records,
        "locale": locale,
        "brand": brand,
        "settings_url": settings_url,
        "recipients": recipients,
    }
    return OutgoingEmail(
        to="",
        subject=translate(
            f"cloud.domain.email_{kind}_subject",
            locale,
            brand=brand,
            domain=org.custom_domain or "",
        ),
        text=_text_body(org, **shared),
        html=_html_body(org, primary_color=primary_color, **shared),
    )
