"""The covering mail a client's report arrives in (issue #300).

Contributed as an ``EmailTemplateKind`` (CLAUDE.md §6), so the agency rewords it in
Instellingen → E-mail like the invoice and quote mails. The workflow this replaces built the
same message as a JavaScript string literal inside a node, which meant changing "Hoi" to
"Beste" was a developer task.

Namespaced ``reporting.report``, asserted unique at mount. A missing override means the
built-in catalog text, so contributing this kind adds no schema and changes nothing until a
tenant types in the box.
"""

from __future__ import annotations

from typing import Any

from app.core.email.kinds import EmailTemplateKind
from app.core.tenancy import RequestContext

REPORT_KIND = "reporting.report"

#: Every one of these is always present at send time. A declared variable that resolves to
#: nothing renders empty — never as a literal ``{period}`` in the client's inbox.
REPORT_VARIABLES: tuple[str, ...] = (
    "brand",
    "client",
    "contact",
    "period",
    "sender_name",
    "link",
)


async def _sample(ctx: RequestContext, locale: str) -> dict[str, str]:  # noqa: ARG001
    """A plausible preview on the org's own identity, the invoicing-kinds pattern."""
    from app.core.email.branding import load_brand

    brand = await load_brand(ctx.session, ctx.org)
    return {
        "brand": brand.brand_name,
        "client": "Acme B.V.",
        "contact": "Jan Jansen",
        "period": "juli 2026",
        "sender_name": ctx.user.full_name or ctx.user.email,
        "link": f"{brand.base_url}/reports",
    }


REPORTING_EMAIL_KINDS: list[EmailTemplateKind] = [
    EmailTemplateKind(
        key=REPORT_KIND,
        module="reporting",
        label_key="reporting.email.kind.report",
        hint_key="reporting.email.kind.report_hint",
        subject_key="reporting.email.report_subject",
        body_key="reporting.email.report_body",
        variables=REPORT_VARIABLES,
        button_key="reporting.email.report_button",
        sample=_sample,
        position=130,
    ),
]


def report_values(report: Any, brand_name: str, sender_name: str, link: str) -> dict[str, str]:
    period = (report.data_snapshot or {}).get("period", {}).get("label", "")
    return {
        "brand": brand_name,
        "client": report.company_name,
        "contact": "",
        "period": period,
        "sender_name": sender_name,
        "link": link,
    }
