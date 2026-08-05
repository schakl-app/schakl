"""Which outgoing mails a tenant may rewrite, and who says so.

#161 gave the tenant an editor for the two **auth** mails, and hardcoded that fact three
times over: the kind list lived in ``models.py``, the catalog keys were built as
``auth.email.{kind}_*``, and one global ``TEMPLATE_VARIABLES`` described what a body could
interpolate. A module wanting its own customisable mail had nowhere to say so — which is why
the invoice, quote and reminder mails, the three an agency's *clients* actually read, were
the only outgoing text on the platform nobody could reword.

So a customisable mail is a **spec**, and a module contributes its own the way it contributes
panels, permissions and impex columns (CLAUDE.md §6): ``ModuleDescriptor.email_templates``.
Core declares core's here and holds no module list. Each spec names its own catalog keys, its
own variables and its own preview values, because those are exactly the three things that are
*not* the same between "reset your password" and "invoice 2026-0142 for € 1.210,00".

Two rules are asserted at mount time (:func:`validate_email_kinds`), for the same reason §17
asserts its impex extensions there:

- **A key is stored data**, so it is unique across core and every module. Two modules landing
  on ``reminder`` would silently share one another's overrides.
- **A module's kinds are namespaced by the module** (``invoicing.invoice``), so a later module
  can add its own reminder mail without a data migration. Core keeps the bare namespace it
  already shipped rows under (``reset``, ``invite``) — renaming those would cost a migration
  to buy nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext

#: ``async (ctx, locale) -> {variable: value}`` — the values a *preview* send substitutes.
#: Async because a plausible preview is tenant data: the invoicing kinds fabricate a document
#: in the org's own currency, exactly as the PDF template editor already does.
SampleProvider = Callable[["RequestContext", str], Awaitable[dict[str, str]]]


@dataclass(frozen=True)
class EmailTemplateKind:
    """One customisable outgoing mail: its catalog defaults, its variables, its preview."""

    #: Stored in ``org_email_templates.kind``. Unique; module kinds are ``<module>.<name>``.
    key: str
    #: i18n key naming this mail in the editor, and one line of when it goes out.
    label_key: str
    hint_key: str
    #: Catalog keys for the built-in (tier 1) subject and **plaintext** body. The plaintext is
    #: what every mail falls back to, so it must stand alone whatever a tenant does to the HTML.
    subject_key: str
    body_key: str
    #: What ``{markers}`` a body may use. Every one of them is always present in the values
    #: dict at send time — a declared variable that resolves to nothing must render empty,
    #: never as a literal ``{reference}`` in the client's inbox.
    variables: tuple[str, ...]
    #: Catalog key of the CTA label, for a body whose ``{link}`` sits on its own line. ``None``
    #: for a mail that carries no link (an invoice carries its PDF).
    button_key: str | None = None
    #: The module whose enablement gates this kind. ``None`` = core, always offered.
    module: str | None = None
    sample: SampleProvider | None = None
    position: int = 100


#: The auth mails' shared variable set (#161): both ride the reset-token mechanism.
AUTH_VARIABLES: tuple[str, ...] = ("brand", "name", "link")


async def _auth_sample(ctx: RequestContext, locale: str) -> dict[str, str]:  # noqa: ARG001
    """A realistic-looking preview on the org's own address; the token is a placeholder."""
    from app.core.email.branding import load_brand

    brand = await load_brand(ctx.session, ctx.org)
    return {
        "brand": brand.brand_name,
        "name": ctx.user.full_name or ctx.user.email,
        "link": f"{brand.base_url}/reset-password?token=preview",
    }


CORE_EMAIL_KINDS: tuple[EmailTemplateKind, ...] = (
    EmailTemplateKind(
        key="invite",
        label_key="settings.email.templates.kind.invite",
        hint_key="settings.email.templates.hint.invite",
        subject_key="auth.email.invite_subject",
        body_key="auth.email.invite_body",
        button_key="auth.email.invite_button",
        variables=AUTH_VARIABLES,
        sample=_auth_sample,
        position=10,
    ),
    EmailTemplateKind(
        key="reset",
        label_key="settings.email.templates.kind.reset",
        hint_key="settings.email.templates.hint.reset",
        subject_key="auth.email.reset_subject",
        body_key="auth.email.reset_body",
        button_key="auth.email.reset_button",
        variables=AUTH_VARIABLES,
        sample=_auth_sample,
        position=20,
    ),
)


def _module_kinds() -> list[EmailTemplateKind]:
    """Every *registered* module's kinds — the send-time view, which needs no org.

    A module is registered because it was imported, and it only sends what it registered, so
    resolving a send against the whole set is right; the *editor* narrows to the org's own
    enabled modules below.
    """
    from app.registry import registry

    return [kind for module in registry.all() for kind in module.email_templates]


def all_email_kinds() -> list[EmailTemplateKind]:
    return sorted([*CORE_EMAIL_KINDS, *_module_kinds()], key=lambda k: (k.position, k.key))


def email_kinds_for(module_names: Sequence[str]) -> list[EmailTemplateKind]:
    """The kinds an org may edit: core's, plus those of the modules it runs.

    Disabling a module takes its mails off the editor, which is the point of routing this
    through the registry rather than letting core name them (the panels rule, §6).
    """
    enabled = set(module_names)
    return sorted(
        [
            *CORE_EMAIL_KINDS,
            *(kind for kind in _module_kinds() if kind.module in enabled),
        ],
        key=lambda k: (k.position, k.key),
    )


def email_kind(key: str) -> EmailTemplateKind | None:
    for kind in all_email_kinds():
        if kind.key == key:
            return kind
    return None


def require_email_kind(key: str) -> EmailTemplateKind:
    kind = email_kind(key)
    if kind is None:
        raise ValueError(f"Unknown email template kind '{key}'")
    return kind


def validate_email_kinds() -> None:
    """Mount-time guard: unique keys, and a module's kinds namespaced by that module.

    A build break, deliberately — both failures are invisible until a tenant's stored override
    starts resolving to the wrong mail.
    """
    from app.registry import registry

    seen: dict[str, str] = {kind.key: "core" for kind in CORE_EMAIL_KINDS}
    for module in registry.all():
        for kind in module.email_templates:
            if kind.key in seen:
                raise ValueError(
                    f"Email template kind '{kind.key}' is declared by both "
                    f"'{seen[kind.key]}' and '{module.name}'"
                )
            if kind.module != module.name:
                raise ValueError(
                    f"Email template kind '{kind.key}' on module '{module.name}' "
                    f"declares module={kind.module!r}"
                )
            if not kind.key.startswith(f"{module.name}."):
                raise ValueError(
                    f"Email template kind '{kind.key}' must be namespaced as "
                    f"'{module.name}.<name>'"
                )
            seen[kind.key] = module.name
