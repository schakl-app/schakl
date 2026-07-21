"""Server-side notification rendering for the e-mail and chat transports (#236).

The web's ``apps/web/src/lib/modules/notifications/format.ts`` is the reference: an event
carries an ``event_type`` and a payload of i18n parameters, and the *reader's* locale decides
the sentence. E-mails and chat lines are rendered server-side against the same shared
catalogs (:mod:`app.i18n`), so this module mirrors that logic — status vocabulary
translation, European dates, minutes → hours — and must stay in step with it.

The sentence keys are written to read after an actor's name ("wees {title} aan je toe"), the
way the in-app feed shows them next to the actor column; system events (a cron reminder) have
no actor and read as full sentences on their own.
"""

from __future__ import annotations

import html as html_lib

from app.i18n import translate

#: Each entity type keeps its status vocabulary in its own namespace (format.ts twin).
_STATUS_NAMESPACE: dict[str, str] = {
    "task": "tasks.status",
    "project": "projects.status",
    "company": "companies.status",
}

#: Date-only payload keys, printed as a European day rather than an ISO string.
_DATE_KEYS = ("due_date", "start_date", "end_date", "week_start")


def _fmt_day(value: str) -> str:
    """``yyyy-mm-dd(…)`` → ``dd-mm-yyyy`` (docs/UX.md: European dates, everywhere)."""
    parts = value[:10].split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = parts
        return f"{day}-{month}-{year}"
    return value


def _fmt_hours(minutes: float, locale: str) -> str:
    """Minutes are the API's unit; hours are what a person reads."""
    text = f"{minutes / 60:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if locale.startswith("nl") else text


def event_sentence(event, actor_name: str | None, locale: str) -> str:  # noqa: ANN001
    """The sentence a notification reads as, in the recipient's locale."""
    payload = dict(event.payload or {})
    namespace = _STATUS_NAMESPACE.get(event.entity_type)
    if namespace:
        for key in ("from", "to"):
            value = payload.get(key)
            if isinstance(value, str):
                payload[key] = translate(f"{namespace}.{value}", locale)
    for key in _DATE_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            payload[key] = _fmt_day(value)
    minutes = payload.get("minutes")
    if isinstance(minutes, (int, float)):
        payload["hours"] = _fmt_hours(minutes, locale)
    key = f"notifications.event.{event.event_type}"
    sentence = translate(key, locale, **payload)
    if sentence == key:
        # An event type without a catalog line (a module ahead of its translations) still
        # produces a readable mail, never a raw i18n key.
        sentence = event.event_type
    return f"{actor_name} {sentence}" if actor_name else sentence


def event_path(event) -> str | None:  # noqa: ANN001
    """Where the notification opens, as a path on the org's own host (format.ts twin)."""
    entity_id = event.entity_id
    if event.entity_type == "task":
        return f"/tasks/{entity_id}"
    if event.entity_type == "project":
        return f"/projects/{entity_id}"
    if event.entity_type == "company":
        return f"/companies/{entity_id}"
    if event.entity_type == "leave_request":
        # A request waiting on *you* opens the team review; a decision about *your* request
        # opens your own list.
        if event.event_type == "leave.requested":
            return f"/leave/team?request={entity_id}"
        return f"/leave?request={entity_id}"
    if event.entity_type == "timesheet":
        return "/time"
    if event.entity_type == "interaction":
        if event.event_type == "interactions.email_pending":
            return "/interactions?status=pending"
        payload = event.payload or {}
        for key, prefix in (
            ("task_id", "/tasks/"),
            ("project_id", "/projects/"),
            ("company_id", "/companies/"),
            ("contact_id", "/contacts/"),
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return f"{prefix}{value}"
    return None


def email_fragment(
    items: list[tuple[str, str | None]], primary_color: str, locale: str
) -> str:
    """The HTML content fragment for a notification mail: one block per item.

    A single deep-linked item gets a CTA button; a digest lists each sentence with a branded
    link. The chrome (logo, card, footer) rides the send seam, not here.
    """
    from app.core.email.branding import button_html, link_html

    label = translate("notifications.email.open", locale)
    blocks: list[str] = []
    if len(items) == 1:
        sentence, href = items[0]
        blocks.append(f'<p style="margin:0 0 16px 0;">{html_lib.escape(sentence)}</p>')
        if href:
            blocks.append(button_html(label, href, primary_color))
        return "\n".join(blocks)
    for sentence, href in items:
        line = html_lib.escape(sentence)
        if href:
            line += "<br>\n" + link_html(label, href, primary_color)
        blocks.append(f'<p style="margin:0 0 20px 0;">{line}</p>')
    return "\n".join(blocks)
