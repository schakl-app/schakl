"""What a minutes design renders against: strings and lists, never rows.

Everything a design prints is resolved here — every date in the org's language, every
paragraph of markdown already turned into sanitised markup, every action item already grouped
by side and then by person, every participant with their initials and, where one is known,
their picture as a ``data:`` URI. A design (ours, or a tenant's own Jinja) only lays it out,
which is what makes a tenant template safe to write and impossible to make disagree with the
review screen about a fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from markupsafe import Markup

from app.core.documents.colors import accent_for, hex_rgb, mix_on_white, rgb_hex
from app.core.richtext import markdown_to_html
from app.i18n import translate
from app.modules.meetings.models import DOCUMENT_SECTIONS
from app.modules.meetings.schemas import MeetingParticipant, MinutesDraft
from app.modules.meetings.transcript import clock

_MONTHS = {
    "nl": (
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}
_WEEKDAYS = {
    "nl": ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}

_LABEL_KEYS = (
    "eyebrow",
    "when",
    "participants",
    "summary",
    "topics",
    "decisions",
    "action_items",
    "open_questions",
    "transcript",
    "evidence",
    "due",
    "task",
    "unverified",
    "for_us",
    "for_client",
    "for_others",
    "nobody",
    "client",
    "project",
    "duration",
    "generated",
    "parts",
    "page",
)


def _lang(locale: str) -> str:
    return (locale or "nl").split("-")[0]


def long_date(value: datetime, locale: str, zone: ZoneInfo) -> str:
    """``dinsdag 22 september 2026 · 14:00`` — the day spelled out, in the document's language,
    on the org's own clock."""
    local = value.astimezone(zone)
    lang = _lang(locale)
    months = _MONTHS.get(lang, _MONTHS["nl"])
    days = _WEEKDAYS.get(lang, _WEEKDAYS["nl"])
    weekday = days[local.weekday()]
    month = months[local.month - 1]
    if lang == "nl":
        return f"{weekday} {local.day} {month} {local.year} · {local:%H:%M}"
    return f"{weekday} {local.day} {month} {local.year} · {local:%H:%M}"


def short_date(value: Any, locale: str) -> str:
    """``30 sep 2026`` / ``Sep 30, 2026`` for a due date."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return value
    lang = _lang(locale)
    months = _MONTHS.get(lang, _MONTHS["nl"])
    month = months[value.month - 1][:3]
    if lang == "nl":
        return f"{value.day} {month} {value.year}"
    return f"{month.capitalize()} {value.day}, {value.year}"


def duration_label(seconds: int | None, locale: str) -> str:
    if not seconds:
        return ""
    minutes = round(seconds / 60)
    if minutes < 60:
        return translate("meetings.duration.minutes", locale, minutes=str(minutes))
    hours, rest = divmod(minutes, 60)
    if rest:
        return translate(
            "meetings.duration.hours_minutes", locale, hours=str(hours), minutes=str(rest)
        )
    return translate("meetings.duration.hours", locale, hours=str(hours))


def initials(name: str) -> str:
    """``Sanne de Vries`` → ``SV``; a bracketed side (``Piet (leverancier)``) is not a name."""
    bare = name.split("(", 1)[0]
    parts = [p for p in bare.replace("-", " ").split() if p and p[0].isalpha()]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _side(participant: MeetingParticipant) -> str:
    if participant.user_id is not None:
        return "agency"
    if participant.contact_id is not None:
        return "client"
    return "other"


def _person(
    name: str,
    *,
    side: str,
    avatar: str | None,
    speaker: str | None = None,
    locale: str,
) -> dict[str, Any]:
    side_key = {"agency": "for_us", "client": "for_client"}.get(side, "for_others")
    return {
        "name": name,
        "initials": initials(name),
        "avatar": avatar,
        "side": side,
        "side_label": translate(f"meetings.doc.role_{side}", locale),
        "speaker": speaker,
        "_side_key": side_key,
    }


def _html(text: str | None, images: dict[str, str] | None = None) -> Markup:
    """Markdown → sanitised markup, drawing only the embedded images the caller resolved."""
    return Markup(markdown_to_html(text, images=images) if text and text.strip() else "")


def _evidence(item: Any, *, show: bool) -> dict[str, Any]:
    """The quote and the clock ride the evidence chapter; the *unverified* mark does not — a
    claim the transcript does not support is a warning the reader needs whatever they ticked."""
    if not show:
        return {"quote": None, "at_label": "", "verified": bool(item.verified)}
    return {
        "quote": (item.quote or "").strip() or None,
        "at_label": clock(item.at) if item.at is not None else "",
        "verified": bool(item.verified),
    }


def group_by_owner(
    items: list[Any],
    people: dict[str, dict[str, Any]],
    locale: str,
    *,
    evidence: bool,
    images: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Action items by side, each side by person — the shape the minutes print in
    (``service.group_action_items``), with every owner resolved to a printable person."""
    from app.modules.meetings.service import group_action_items

    blocks: list[dict[str, Any]] = []
    for side, groups in group_action_items(items):
        printed_groups = []
        for owner_key, owned in groups:
            owner = people.get(owner_key or "")
            if owner is None:
                label = ""
                if owned and owned[0].owner_label:
                    label = owned[0].owner_label.strip()
                elif owner_key and owner_key.startswith("n:"):
                    label = owner_key[2:]
                owner = _person(label, side=side, avatar=None, locale=locale) if label else None
            printed_groups.append(
                {
                    "owner": owner,
                    # ``entries``, not ``items``: Jinja resolves ``group.items`` to the dict
                    # method before the key, and a design would iterate a method.
                    "entries": [
                        {
                            "title": item.title.strip(),
                            "description_html": _html(item.description, images),
                            "due_label": short_date(item.due_date, locale),
                            "is_task": item.task_id is not None,
                            **_evidence(item, show=evidence),
                        }
                        for item in owned
                    ],
                }
            )
        blocks.append(
            {
                "side": side,
                "side_label": translate(f"meetings.minutes.side_{side}", locale),
                "groups": printed_groups,
            }
        )
    return blocks


def _speaker_name(label: Any, speakers: dict[str, str], locale: str) -> str:
    """A transcript line's speaker as a person: the roster's name for the label, or — where
    nobody was paired with it — "Spreker 2", never the provider's bare ``S2``."""
    if not label:
        return ""
    label = str(label)
    if speakers.get(label):
        return speakers[label]
    digits = "".join(ch for ch in label if ch.isdigit())
    return translate("meetings.doc.speaker_n", locale, n=digits or label)


def build_context(
    *,
    title: str,
    kind: str,
    status: str,
    occurred_at: datetime,
    duration_seconds: int | None,
    owner_name: str | None,
    client: str | None,
    project: str | None,
    participants: list[MeetingParticipant],
    people: dict[str, dict[str, Any]],
    minutes: MinutesDraft | None,
    segments: list[dict[str, Any]],
    transcript_text: str | None,
    transcript_parts: int,
    speakers: dict[str, str],
    sections: list[str],
    brand_name: str,
    logo_uri: str | None,
    cover_uri: str | None,
    client_logo_uri: str | None,
    accent: str | None,
    brand_color: str | None,
    footer_text: str | None,
    show_avatars: bool,
    images: dict[str, str] | None = None,
    locale: str,
    zone: ZoneInfo,
    generated_at: datetime,
) -> dict[str, Any]:
    """The whole document as data. ``people`` maps ``u:<id>`` / ``c:<id>`` to a printable
    person (name, initials, avatar) — resolved by the caller, which has the session."""
    accent_hex = accent_for(accent, brand_color)
    accent_rgb = hex_rgb(accent_hex, (79, 70, 229))
    wanted = [key for key in DOCUMENT_SECTIONS if key in sections]
    show = {key: key in wanted for key in DOCUMENT_SECTIONS}
    draft = minutes or MinutesDraft()
    evidence = show["evidence"]

    roster = []
    for p in participants:
        key = f"u:{p.user_id}" if p.user_id else (f"c:{p.contact_id}" if p.contact_id else "")
        person = people.get(key) or _person(p.name, side=_side(p), avatar=None, locale=locale)
        person = dict(person)
        person["speaker"] = p.speaker
        if not show_avatars:
            person["avatar"] = None
        roster.append(person)
    # Who is ours and who is theirs, said by grouping rather than by a word under each name:
    # the agency's people under the agency's name, the client's under the client's.
    side_titles = {
        "agency": brand_name,
        "client": client or translate("meetings.doc.role_client", locale),
        "other": translate("meetings.doc.role_other", locale),
    }
    roster_groups = [
        {
            "side": side,
            "title": side_titles[side],
            "people": [p for p in roster if p["side"] == side],
        }
        for side in ("agency", "client", "other")
        if any(p["side"] == side for p in roster)
    ]

    if not show_avatars:
        people = {k: {**v, "avatar": None} for k, v in people.items()}

    labels = {key: translate(f"meetings.doc.{key}", locale) for key in _LABEL_KEYS}
    kind_label = translate(f"meetings.kind.{kind}", locale)
    return {
        "locale": locale,
        "brand_name": brand_name,
        "logo": logo_uri,
        "cover": cover_uri,
        "client_logo": client_logo_uri,
        "accent": accent_hex,
        "accent_soft": rgb_hex(mix_on_white(accent_rgb, 0.08)),
        "accent_mid": rgb_hex(mix_on_white(accent_rgb, 0.22)),
        "title": (draft.title or "").strip() or title,
        "kind": kind,
        "kind_label": kind_label,
        "status": status,
        # Once printed "Vastgesteld" / "Concept"; the minutes are neither now (there is no
        # confirm step). Kept as an empty string so a tenant's own template that names it
        # still renders.
        "status_label": "",
        "occurred_label": long_date(occurred_at, locale, zone),
        "duration_label": duration_label(duration_seconds, locale),
        "owner": owner_name or "",
        "roster_groups": roster_groups,
        "client": client or "",
        "project": project or "",
        "participants": roster,
        "summary_html": _html(draft.summary, images),
        "topics": [
            {"heading": t.heading.strip(), "html": _html(t.text, images)} for t in draft.topics
        ],
        "decisions": [
            {"text": d.text.strip(), "html": _html(d.text, images), **_evidence(d, show=evidence)}
            for d in draft.decisions
        ],
        "action_blocks": group_by_owner(
            draft.action_items, people, locale, evidence=evidence, images=images
        ),
        "action_count": len(draft.action_items),
        "open_questions": [q.strip() for q in draft.open_questions if q.strip()],
        "open_questions_html": [_html(q, images) for q in draft.open_questions if q.strip()],
        "transcript": [
            {
                "at_label": clock(float(s.get("start") or 0)),
                "speaker": _speaker_name(s.get("speaker"), speakers, locale),
                "text": str(s.get("text") or ""),
            }
            for s in segments
            if isinstance(s, dict)
        ],
        "transcript_text": (transcript_text or "").strip(),
        "transcript_parts": transcript_parts,
        "sections": wanted,
        "show": show,
        "footer_text": (footer_text or "").strip() or None,
        "generated_label": long_date(generated_at, locale, zone),
        "labels": labels,
        # Helpers a tenant's own template gets too.
        "fmt_clock": clock,
        "fmt_date": lambda value: short_date(value, locale),
    }


__all__ = ["build_context", "duration_label", "initials", "long_date", "short_date"]
