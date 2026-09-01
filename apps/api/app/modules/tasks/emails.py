"""The mail a client's contact gets when a task is assigned to them (#454).

``task.assigned`` fans out to **users** through the notifications module, which drops every
portal login by design — so a contact assigned a task (#273) heard nothing, with or without a
login. This is the one mail that reaches them, and because it is a mail the agency's *client*
reads it is theirs to reword: an ``EmailTemplateKind`` on the module descriptor (CLAUDE.md §6,
``docs/EMAIL.md``), namespaced ``tasks.assigned_contact``, with a missing override meaning the
built-in catalog text — contributing it adds no schema and changes nothing until a tenant types
in the box.

Two rules decide *whether* it goes out, and both are answered in the worker rather than in the
request:

* **Only to a contact who holds an active portal login.** The mail carries a link into the
  portal, and a link to a login they do not have is #253's control that always refuses, printed
  in an inbox. A contact with no login, or a disabled one, gets nothing — the agency's own
  channels are how they hear about it.
* **Never inside the assignment's transaction.** The request queues one job (inside
  ``release_db``, so the row is committed before a worker can read it — the reporting runner's
  lesson) and the worker composes and sends with its own session through ``send_org_email``,
  the seam every branded mail leaves by. A transport that is down is logged, and the assignment
  it rode on has long since been saved.

The person's identity is resolved through the portal-subject seam (``app/core/portal.py``):
the contacts module answers "who is this, and do they have a login", and this module names no
other module's table (§6). The client's name comes from one org-scoped SQL read for the same
reason.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.email.branding import EmailBrand, load_brand
from app.core.email.kinds import EmailTemplateKind
from app.core.email.senders import OutgoingEmail
from app.core.email.service import send_org_email
from app.core.email.templates import build_email_content, resolve_template
from app.core.jobs import system_context
from app.core.models import Org, OrgSettings
from app.core.portal import portal_subject_provider
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.i18n import resolve_locale
from app.modules.tasks.models import Task

logger = logging.getLogger("schakl.tasks")

ASSIGNED_CONTACT_KIND = "tasks.assigned_contact"

#: Every one of these is always present at send time (``docs/EMAIL.md``): a declared variable
#: that resolves to nothing renders empty, never as a literal ``{due_date}`` in an inbox.
ASSIGNED_CONTACT_VARIABLES: tuple[str, ...] = (
    "brand",
    "name",
    "title",
    "due_date",
    "company",
    "link",
)


def _fmt_date(value: Any) -> str:
    """European dd-mm-yyyy — the product's date language everywhere (docs/UX.md)."""
    return value.strftime("%d-%m-%Y") if value else ""


async def _sample(ctx: RequestContext, locale: str) -> dict[str, str]:  # noqa: ARG001
    """A plausible preview on the org's own identity — the invoicing kinds' pattern."""
    brand = await load_brand(ctx.session, ctx.org)
    return {
        "brand": brand.brand_name,
        "name": "Jan Jansen",
        "title": "Fotomateriaal aanleveren",
        "due_date": "15-09-2026",
        "company": "Acme B.V.",
        "link": f"{brand.base_url}/tasks",
    }


TASK_EMAIL_KINDS: list[EmailTemplateKind] = [
    EmailTemplateKind(
        key=ASSIGNED_CONTACT_KIND,
        module="tasks",
        label_key="tasks.email.kind.assigned_contact",
        hint_key="tasks.email.kind.assigned_contact_hint",
        subject_key="tasks.email.assigned_contact_subject",
        body_key="tasks.email.assigned_contact_body",
        variables=ASSIGNED_CONTACT_VARIABLES,
        button_key="tasks.email.assigned_contact_button",
        sample=_sample,
        position=140,
    ),
]


async def compose_assigned_contact(
    session: AsyncSession,
    org: Org,
    task: Task,
    *,
    to: str,
    name: str,
    company: str,
    locale: str,
) -> tuple[OutgoingEmail, EmailBrand]:
    """The mail, in the tenant's words if they wrote any, else the catalog's — composed while
    the session is still ours (an override is an org-scoped read, ``docs/EMAIL.md``)."""
    brand = await load_brand(session, org)
    values = {
        "brand": brand.brand_name,
        "name": name,
        "title": task.title,
        "due_date": _fmt_date(task.due_date),
        "company": company,
        "link": f"{brand.base_url}/tasks/{task.id}",
    }
    template = await resolve_template(session, org.id, ASSIGNED_CONTACT_KIND, locale)
    subject, text, html = build_email_content(
        ASSIGNED_CONTACT_KIND,
        locale,
        template.subject if template else None,
        template.body_html if template else None,
        values,
        primary_color=brand.primary_color,
    )
    return OutgoingEmail(to=to, subject=subject, text=text, html=html), brand


async def contact_login(ctx: RequestContext, contact_id: uuid.UUID) -> tuple[str, str] | None:
    """``(email, name)`` of the contact's **active** portal login, or ``None``.

    Through the portal-subject seam: the contacts module says who the row is and which login
    it carries; whether that login may still sign in is ``users.is_active`` (the portal's own
    "disabled" flag, ``docs/PORTAL.md``). Any other answer is *no mail*.
    """
    provider = portal_subject_provider("contact")
    if provider is None:
        return None
    subject = await provider.load(ctx, contact_id)
    if subject is None or subject.user_id is None or not subject.email:
        return None
    user = await ctx.session.get(User, subject.user_id)
    if user is None or not user.is_active:
        return None
    return subject.email, subject.display_name or ""


async def tasks_send_contact_assigned(ctx: dict, org_id: str, task_id: str) -> None:  # noqa: ARG001
    """Worker: mail the contact a task was just assigned to (#454), if they can open it."""
    async with async_session_maker() as session:
        org = await session.get(Org, uuid.UUID(org_id))
        if org is None:
            return
        await set_current_org(session, org.id)
        task = await session.scalar(
            select(Task).where(Task.org_id == org.id, Task.id == uuid.UUID(task_id))
        )
        if task is None or task.assignee_contact_id is None:
            # Reassigned or deleted between the queue and the pickup: nothing to say.
            return
        recipient = await contact_login(system_context(org, session), task.assignee_contact_id)
        if recipient is None:
            return
        to, name = recipient
        company = ""
        if task.company_id is not None:
            company = (
                await session.scalar(
                    sql_text("SELECT name FROM companies WHERE org_id = :oid AND id = :cid"),
                    {"oid": org.id, "cid": task.company_id},
                )
            ) or ""
        default_locale = await session.scalar(
            select(OrgSettings.default_locale).where(OrgSettings.org_id == org.id)
        )
        # The client reads the tenant's product in the tenant's language, never a colleague's.
        locale = resolve_locale(None, default_locale)
        message, brand = await compose_assigned_contact(
            session, org, task, to=to, name=name, company=company, locale=locale
        )
        ok, error = await send_org_email(session, org.id, message, brand=brand)
        if not ok:
            logger.warning(
                "tasks: contact-assignment mail for task %s not sent: %s", task.id, error
            )
