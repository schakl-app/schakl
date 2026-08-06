"""Org e-mail settings service + the one send seam every consumer goes through (#17)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.email.branding import EmailBrand, apply_branding, load_brand, load_brand_by_id
from app.core.email.kinds import EmailTemplateKind, email_kinds_for
from app.core.email.models import EmailSettings, OrgEmailTemplate
from app.core.email.schemas import (
    EmailSettingsRead,
    EmailSettingsWrite,
    EmailTemplateItem,
    EmailTemplateKindRead,
    EmailTemplatesRead,
    EmailTemplateTest,
    EmailTemplateWrite,
    EmailTestResult,
)
from app.core.email.senders import OutgoingEmail, Sender, send_email
from app.core.email.templates import (
    apply_signature,
    build_email_content,
    default_body_html,
    default_subject,
    is_supported_locale,
    sanitize_email_html,
)
from app.core.models import Org, OrgSettings
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.i18n import available_locales, resolve_locale, translate

#: Which config keys each provider stores, and which of them is the secret.
_PROVIDER_FIELDS: dict[str, tuple[tuple[str, ...], str]] = {
    "smtp": (("host", "port", "security", "username", "password"), "password"),
    "brevo": (("api_key",), "api_key"),
    "sendgrid": (("api_key",), "api_key"),
    "smtp2go": (("api_key",), "api_key"),
}


def _config_of(row: EmailSettings) -> dict:
    return json.loads(decrypt(row.config_enc))


async def get_row(session: AsyncSession, org_id) -> EmailSettings | None:  # noqa: ANN001
    return await session.scalar(select(EmailSettings).where(EmailSettings.org_id == org_id))


def _instance_transport() -> tuple[str, dict]:
    """The operator-provided transport from config (SCHAKL_INSTANCE_EMAIL_*) in the shape
    :func:`send_email` expects."""
    provider = settings.instance_email_provider
    if provider == "smtp":
        config = {
            "host": settings.instance_email_host,
            "port": settings.instance_email_port,
            "security": settings.instance_email_security,
            "username": settings.instance_email_username,
            "password": settings.instance_email_password,
        }
    else:
        config = {"api_key": settings.instance_email_api_key}
    return provider, config


async def _org_brand(session: AsyncSession, org_id) -> str | None:  # noqa: ANN001
    """Best-effort display name for instance-transport sends. RLS-scoped: callers of the
    send seam run in a tenant transaction with the GUC bound to this org."""
    try:
        return await session.scalar(
            select(OrgSettings.brand_name).where(OrgSettings.org_id == org_id)
        )
    except Exception:  # noqa: BLE001 — a missing brand must never block a mail
        return None


async def _send_instance_email(
    session: AsyncSession,
    org_id,  # noqa: ANN001
    message: OutgoingEmail,
    *,
    from_name: str | None = None,
    reply_to: str | None = None,
) -> tuple[bool, str | None]:
    """Send via the operator's transport: the *instance's* from-address (SPF/DKIM belong to
    the operator's domain), displayed as the org's brand."""
    provider, config = _instance_transport()
    brand = (
        from_name
        or await _org_brand(session, org_id)
        or settings.instance_email_from_name
        or "schakl"
    )
    sender = Sender(
        from_email=str(settings.instance_email_from),
        from_name=brand,
        reply_to=reply_to,
    )
    return await send_email(provider, config, sender, message)


def instance_email_allowed_for(org: Org) -> bool:
    """May *this org* send through the operator's transport (epic #199)?

    Two facts, and both must hold: the instance has a transport configured at all
    (``SCHAKL_INSTANCE_EMAIL_*``), and the org is entitled to it (``orgs.email_included``,
    the operator's per-org choice — true unless they said otherwise). Takes the org row
    because a request context already holds one; the send seam has only an id and uses the
    async twin below.
    """
    return settings.instance_email_available and org.email_included


async def instance_email_allowed(session: AsyncSession, org_id) -> bool:  # noqa: ANN001
    """:func:`instance_email_allowed_for` from an org id — the send seam's shape.

    The instance check runs first so the common self-hosted case answers without a query;
    ``orgs`` carries no RLS (CLAUDE.md §5), so the read is safe from any session.
    """
    if not settings.instance_email_available:
        return False
    included = await session.scalar(select(Org.email_included).where(Org.id == org_id))
    # A row that has gone missing is not an entitlement — but nor is it this seam's business
    # to fail loudly about; treat only an explicit False as "not included".
    return included is not False


async def email_configured(session: AsyncSession, org_id) -> bool:  # noqa: ANN001
    """Whether a send for this org can go *somewhere* — its own transport, or the
    instance-provided fallback (the cloud "included e-mail", epic #199)."""
    if await instance_email_allowed(session, org_id):
        return True
    return await get_row(session, org_id) is not None


async def send_org_email(
    session: AsyncSession,
    org_id,
    message: OutgoingEmail,  # noqa: ANN001
    *,
    brand: EmailBrand | None = None,
) -> tuple[bool, str | None]:
    """Send through the org's configured transport; ``(False, key)`` when none is configured.

    Resolution order (epic #199): an org's own row wins; a row with ``provider="instance"``
    — or no row at all while the instance transport is available *to this org* — goes
    through the operator-provided transport. The error string for a missing configuration is
    an i18n key (CLAUDE.md §9) so callers can surface it directly; provider failures carry
    the provider's own text.
    """
    row = await get_row(session, org_id)
    # Only the two instance-transport paths need the entitlement, and it costs a query — so
    # it is resolved here rather than on every send that already has its own provider. A
    # stored "instance" choice the org is no longer entitled to (the operator turned the
    # transport off, or took this org off included e-mail) sends nowhere, never elsewhere.
    if (row is None or row.provider == "instance") and not await instance_email_allowed(
        session, org_id
    ):
        return False, "errors.email_not_configured"
    # The org-wide signature rides the one send seam (owner request): every outgoing mail —
    # auth, notification, invoice — carries it automatically, with no per-caller code.
    if row is not None:
        message = apply_signature(message, row.signature_html)
    # So does the tenant's branded chrome (#236): whatever HTML (or promoted text) the mail
    # carries leaves wrapped in the org's logo/colors. Pass ``brand`` to skip the re-read
    # when the caller already resolved it; a failed resolve sends the mail unwrapped.
    if brand is None:
        brand = await load_brand_by_id(session, org_id)
    message = apply_branding(brand, message)
    if row is None:
        return await _send_instance_email(session, org_id, message)
    if row.provider == "instance":
        return await _send_instance_email(
            session, org_id, message, from_name=row.from_name, reply_to=row.reply_to
        )
    sender = Sender(from_email=row.from_email, from_name=row.from_name, reply_to=row.reply_to)
    return await send_email(row.provider, _config_of(row), sender, message)


def _clean_signature(raw: str | None) -> str | None:
    """Sanitise the org signature on write (the templates' rule); blank clears it."""
    if raw is None or not raw.strip():
        return None
    cleaned = sanitize_email_html(raw)
    return cleaned if cleaned.strip() else None


class EmailSettingsService:
    """Admin surface: one settings row per org, secrets write-only."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    def _stored(self, row: EmailSettings) -> EmailSettingsRead:
        if row.provider == "instance":
            # No credentials of its own — the transport lives in instance config (#199).
            return EmailSettingsRead(
                provider="instance",
                from_email=settings.instance_email_from or "",
                from_name=row.from_name,
                reply_to=row.reply_to,
                signature_html=row.signature_html,
            )
        config = _config_of(row)
        keys, secret_key = _PROVIDER_FIELDS[row.provider]
        public = {k: config.get(k) for k in keys if k != secret_key}
        return EmailSettingsRead(
            provider=row.provider,  # type: ignore[arg-type]
            from_email=row.from_email,
            from_name=row.from_name,
            reply_to=row.reply_to,
            has_secret=bool(config.get(secret_key)),
            signature_html=row.signature_html,
            **public,
        )

    async def _resolved(self, row: EmailSettings | None) -> EmailSettingsRead:
        """The stored configuration plus **what would actually send right now**.

        The two are not the same thing, and the screen that only knew the first one lied by
        omission: an org on cloud's included e-mail (epic #199) stores nothing, so the page
        read "not configured" and offered a blank SMTP form while every mail was leaving
        happily through the operator's transport. So the read states the active transport —
        and, because the org never entered them, the address and name it sends as.
        """
        read = self._stored(row) if row is not None else EmailSettingsRead()
        # The org row is already in hand (``require_context`` loaded it), so this settings
        # surface costs no extra query for the entitlement.
        read.instance_email_available = instance_email_allowed_for(self.ctx.org)
        if row is not None and row.provider != "instance":
            read.active_provider = row.provider  # type: ignore[assignment]
            read.active_from_email = row.from_email
            read.active_from_name = row.from_name
            return read
        # Either the explicit "included e-mail" choice or no row at all — both send through
        # the operator's transport, and both stop sending the moment it is withdrawn.
        if not read.instance_email_available:
            return read
        read.active_provider = "instance"
        read.active_from_email = settings.instance_email_from or None
        read.active_from_name = (
            row.from_name
            if row is not None
            else (
                await _org_brand(self.ctx.session, self.ctx.org.id)
                or settings.instance_email_from_name
                or None
            )
        )
        return read

    async def get(self) -> EmailSettingsRead:
        self.ctx.require("settings.email.manage")
        return await self._resolved(await get_row(self.ctx.session, self.ctx.org.id))

    async def save(self, data: EmailSettingsWrite) -> EmailSettingsRead:
        self.ctx.require("settings.email.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if data.provider == "instance":
            # The explicit "included e-mail" choice: only offerable while the operator's
            # transport is configured *and* this org is entitled to it; stores no credentials.
            if not instance_email_allowed_for(self.ctx.org):
                raise AppError(
                    "conflict", "errors.instance_email_unavailable", status_code=409
                )
            values = {
                "provider": "instance",
                "config_enc": encrypt(json.dumps({})),
                "from_email": settings.instance_email_from or "",
                "from_name": data.from_name,
                "reply_to": str(data.reply_to) if data.reply_to else None,
                "signature_html": _clean_signature(data.signature_html),
            }
            if row is None:
                row = EmailSettings(org_id=self.ctx.org.id, **values)
                self.ctx.session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await self.ctx.session.flush()
            return await self._resolved(row)
        if data.from_email is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"from_email": "errors.required"},
            )
        keys, secret_key = _PROVIDER_FIELDS[data.provider]

        config = {k: getattr(data, k) for k in keys if getattr(data, k) not in (None, "")}
        # An empty secret on an update means "keep what is stored" — the form never sees it back.
        if secret_key not in config and row is not None and row.provider == data.provider:
            stored = _config_of(row).get(secret_key)
            if stored:
                config[secret_key] = stored
        if data.provider == "smtp" and not config.get("host"):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"host": "errors.required"},
            )
        if not config.get(secret_key) and data.provider != "smtp":
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"api_key": "errors.required"},
            )

        values = {
            "provider": data.provider,
            "config_enc": encrypt(json.dumps(config)),
            "from_email": str(data.from_email),
            "from_name": data.from_name,
            "reply_to": str(data.reply_to) if data.reply_to else None,
            "signature_html": _clean_signature(data.signature_html),
        }
        if row is None:
            row = EmailSettings(org_id=self.ctx.org.id, **values)
            self.ctx.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self.ctx.session.flush()
        return await self._resolved(row)

    async def delete(self) -> None:
        """Remove the configuration — e-mail is simply off again."""
        self.ctx.require("settings.email.manage")
        row = await get_row(self.ctx.session, self.ctx.org.id)
        if row is not None:
            await self.ctx.session.delete(row)
            await self.ctx.session.flush()

    async def test(self) -> EmailTestResult:
        """Send a test mail to the acting admin via the *stored* settings — an explicit admin
        action, the one place this surface does synchronous network I/O."""
        self.ctx.require("settings.email.manage")
        brand = await load_brand(self.ctx.session, self.ctx.org)
        locale = resolve_locale(getattr(self.ctx.user, "locale", None))
        message = OutgoingEmail(
            to=self.ctx.user.email,
            subject=translate("settings.email.test_subject", locale, brand=brand.brand_name),
            text=translate("settings.email.test_body", locale, brand=brand.brand_name),
        )
        try:
            ok, error = await send_org_email(
                self.ctx.session, self.ctx.org.id, message, brand=brand
            )
        except Exception as exc:  # noqa: BLE001 - surface the provider failure, don't 500
            return EmailTestResult(ok=False, error=str(exc))
        return EmailTestResult(ok=ok, error=error)


class OrgEmailTemplateService:
    """Admin surface for the tenant-customisable email templates (#161 tier 2).

    Same permission and page as the transport (``settings.email.manage``, Instellingen ->
    E-mail): a template can leak nothing a transport config does not, and both are the same
    "how this tenant emails people" concern.

    *Which* mails are on offer is not this service's to know: core's auth pair plus whatever
    the org's enabled modules contribute (:mod:`app.core.email.kinds`). Disabling invoicing
    takes its three mails off the editor — and off the write path, so a stale form cannot
    store an override for a mail this org no longer sends.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _stored(self) -> dict[tuple[str, str], OrgEmailTemplate]:
        rows = (
            await self.ctx.session.execute(
                select(OrgEmailTemplate).where(OrgEmailTemplate.org_id == self.ctx.org.id)
            )
        ).scalars().all()
        return {(row.kind, row.locale): row for row in rows}

    async def _kinds(self) -> list[EmailTemplateKind]:
        """The mails *this* org may rewrite. One read of its module list (the same
        org_settings fallback ``/meta/tenant`` uses), on an admin screen."""
        enabled = await self.ctx.session.scalar(
            select(OrgSettings.enabled_modules).where(OrgSettings.org_id == self.ctx.org.id)
        )
        return email_kinds_for(enabled or list(settings.enabled_modules))

    async def _validated(self, kind: str, locale: str) -> EmailTemplateKind:
        spec = next((k for k in await self._kinds() if k.key == kind), None)
        if spec is None:
            raise AppError(
                "validation", "errors.validation", status_code=422,
                fields={"kind": "errors.validation"},
            )
        if not is_supported_locale(locale):
            raise AppError(
                "validation", "errors.validation", status_code=422,
                fields={"locale": "errors.validation"},
            )
        return spec

    async def list(self) -> EmailTemplatesRead:
        self.ctx.require("settings.email.manage")
        stored = await self._stored()
        locales = available_locales()
        kinds = await self._kinds()
        items: list[EmailTemplateItem] = []
        for spec in kinds:
            for locale in locales:
                row = stored.get((spec.key, locale))
                items.append(
                    EmailTemplateItem(
                        kind=spec.key,
                        locale=locale,
                        subject=row.subject if row else None,
                        body_html=row.body_html if row else None,
                        default_subject=default_subject(spec.key, locale),
                        default_body_html=default_body_html(spec.key, locale),
                    )
                )
        return EmailTemplatesRead(
            locales=locales,
            kinds=[
                EmailTemplateKindRead(
                    key=spec.key,
                    label_key=spec.label_key,
                    hint_key=spec.hint_key,
                    variables=list(spec.variables),
                    module=spec.module,
                )
                for spec in kinds
            ],
            templates=items,
        )

    async def save(self, data: EmailTemplateWrite) -> EmailTemplateItem:
        self.ctx.require("settings.email.manage")
        await self._validated(data.kind, data.locale)
        subject = (data.subject or "").strip() or None
        body_html = (data.body_html or "").strip() or None
        if body_html is not None:
            # Sanitise on write too, so a stored value (and any preview of it) is already safe.
            body_html = sanitize_email_html(body_html)

        row = await self.ctx.session.scalar(
            select(OrgEmailTemplate).where(
                OrgEmailTemplate.org_id == self.ctx.org.id,
                OrgEmailTemplate.kind == data.kind,
                OrgEmailTemplate.locale == data.locale,
            )
        )
        if subject is None and body_html is None:
            # Blank both = reset to the built-in default (delete the override).
            if row is not None:
                await self.ctx.session.delete(row)
                await self.ctx.session.flush()
        elif row is None:
            row = OrgEmailTemplate(
                org_id=self.ctx.org.id,
                kind=data.kind,
                locale=data.locale,
                subject=subject,
                body_html=body_html,
            )
            self.ctx.session.add(row)
            await self.ctx.session.flush()
        else:
            row.subject = subject
            row.body_html = body_html
            await self.ctx.session.flush()

        return EmailTemplateItem(
            kind=data.kind,
            locale=data.locale,
            subject=subject,
            body_html=body_html,
            default_subject=default_subject(data.kind, data.locale),
            default_body_html=default_body_html(data.kind, data.locale),
        )

    async def test(self, data: EmailTemplateTest) -> EmailTestResult:
        """Render the draft (or stored/default) with sample values and send to the acting admin.

        The values come from the **kind's own** sample provider, because plausible is the whole
        point of a preview: a reset mail wants a link on the org's address, an invoice mail a
        number and an amount in the org's currency. A kind that provides none previews with the
        brand alone rather than not at all.
        """
        self.ctx.require("settings.email.manage")
        spec = await self._validated(data.kind, data.locale)
        locale = resolve_locale(data.locale)
        brand = await load_brand(self.ctx.session, self.ctx.org)
        values = {"brand": brand.brand_name}
        if spec.sample is not None:
            values.update(await spec.sample(self.ctx, locale))
        # Every declared variable resolves to *something*: an unfilled one would reach the
        # inbox as a literal "{reference}".
        values = {name: values.get(name, "") for name in spec.variables} | values
        subject, text, html = build_email_content(
            data.kind,
            locale,
            data.subject,
            data.body_html,
            values,
            primary_color=brand.primary_color,
        )
        message = OutgoingEmail(to=self.ctx.user.email, subject=subject, text=text, html=html)
        try:
            ok, error = await send_org_email(
                self.ctx.session, self.ctx.org.id, message, brand=brand
            )
        except Exception as exc:  # noqa: BLE001 - surface the provider failure, don't 500
            return EmailTestResult(ok=False, error=str(exc))
        return EmailTestResult(ok=ok, error=error)
