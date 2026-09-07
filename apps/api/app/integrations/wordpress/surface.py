"""A connected WordPress as a **surface**: its content, its forms, its abilities, its facts.

Every route this file serves is one of the tools an agency's staff reach through schakl's own
MCP (docs/WORDPRESS.md §7). The design rule it exists to state: **a client site is a parameter,
never a tool.** Forty connected sites add forty rows to ``/wordpress/sites`` and zero tools to
``/mcp/wordpress``, because a tool is a route and the site is an argument of the route. What
differs per site — which plugins it has, which post types, which abilities — is *answered*
(``summary``, ``abilities``) rather than encoded in the tool list, so a chat client's context
budget is the same whether the agency holds four sites or four hundred (docs/MCP.md).

Four rules the routes share.

* **The credential is resolved from our row, under our permission check, for our caller's
  org.** Never from anything the caller sent (§12's confused deputy, in the outward direction).
* **Every outbound call runs inside ``ctx.release_db()``** and every refusal the site makes is
  mapped to the envelope with the site's own words carried as evidence — a 404 from the site is
  our 404, a validation refusal our 422, and everything else a 502 that says which.
* **The audience decides the permission** (``permissions.py``): a draft is ``content.write``,
  anything a visitor can see is ``content.publish``, a form is live the moment it saves, and an
  ability runs on ``ability.read`` only if it *says* it is read-only.
* **Every write is a trail line on the site row** (§16): what was changed, on which record,
  by whom — the one thing the client's own WordPress does not record about us.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.crypto import decrypt
from app.errors import AppError
from app.integrations.wordpress.client import (
    CORE_ENVIRONMENT_INFO,
    CORE_SITE_INFO,
    WordPressAuthError,
    WordPressClient,
    WordPressError,
    WordPressUnreachable,
    describe_failure,
)
from app.integrations.wordpress.models import WordPressSite
from app.integrations.wordpress.schemas import (
    LIVE_STATUSES,
    WordPressAbility,
    WordPressAbilityResult,
    WordPressAbilityRun,
    WordPressContentCreate,
    WordPressContentList,
    WordPressContentRead,
    WordPressContentRow,
    WordPressContentType,
    WordPressContentWrite,
    WordPressFormCreate,
    WordPressFormMail,
    WordPressFormRead,
    WordPressFormRow,
    WordPressFormWrite,
    WordPressMediaList,
    WordPressMediaRow,
    WordPressSiteSummary,
)
from app.integrations.wordpress.service import ENTITY_TYPE, WordPressService

T = TypeVar("T")

#: The two post types every WordPress has, on the REST bases core fixes for them. Anything else
#: is looked up in the site's own ``/types`` — a custom post type's base is the site's choice.
_CORE_BASES = {"page": "pages", "post": "posts"}

#: A list read is bounded whatever the caller asks (§17: capped, never truncated silently — the
#: response carries the site's own ``total`` beside what was shown).
_MAX_PER_PAGE = 100

#: Namespaces that say what the site carries. ``wpml/v1`` is what puts a language on a record.
_NS_FORMS = "contact-form-7/v1"
_NS_ABILITIES = "wp-abilities/v1"
_NS_WPML = "wpml/v1"


def _text(value: Any, key: str = "raw") -> str:
    """WordPress renders ``title``/``content``/``excerpt`` as ``{raw, rendered}`` under
    ``context=edit`` and as ``{rendered}`` otherwise; a plugin may flatten one to a string."""
    if isinstance(value, dict):
        raw = value.get(key)
        if isinstance(raw, str):
            return raw
        rendered = value.get("rendered")
        return rendered if isinstance(rendered, str) else ""
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _lang(raw: dict[str, Any]) -> str | None:
    """WPML's language on a record, under whichever key its REST filter put it."""
    for key in ("lang", "wpml_current_locale", "language_code"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _content_row(type_slug: str, raw: dict[str, Any]) -> WordPressContentRow:
    return WordPressContentRow(
        id=int(raw.get("id") or 0),
        type=str(raw.get("type") or type_slug),
        slug=str(raw.get("slug") or ""),
        title=_text(raw.get("title")),
        status=str(raw.get("status") or ""),
        link=raw.get("link") if isinstance(raw.get("link"), str) else None,
        modified=raw.get("modified_gmt") or raw.get("modified"),
        parent=_int(raw.get("parent")) or None,
        lang=_lang(raw),
    )


def _content_read(type_slug: str, raw: dict[str, Any]) -> WordPressContentRead:
    row = _content_row(type_slug, raw)
    acf = raw.get("acf")
    meta = raw.get("meta")
    return WordPressContentRead(
        **row.model_dump(),
        content=_text(raw.get("content")),
        rendered=_text(raw.get("content"), "rendered"),
        excerpt=_text(raw.get("excerpt")),
        template=raw.get("template") if isinstance(raw.get("template"), str) else None,
        acf=acf if isinstance(acf, dict) else None,
        meta=meta if isinstance(meta, dict) else {},
        featured_media=_int(raw.get("featured_media")) or None,
        author=_int(raw.get("author")),
        date=raw.get("date_gmt") or raw.get("date"),
    )


def _media_row(raw: dict[str, Any]) -> WordPressMediaRow:
    details = raw.get("media_details") if isinstance(raw.get("media_details"), dict) else {}
    return WordPressMediaRow(
        id=int(raw.get("id") or 0),
        title=_text(raw.get("title"), "rendered") or _text(raw.get("title")),
        source_url=str(raw.get("source_url") or ""),
        mime_type=raw.get("mime_type") if isinstance(raw.get("mime_type"), str) else None,
        alt=raw.get("alt_text") if isinstance(raw.get("alt_text"), str) else "",
        width=_int(details.get("width")),
        height=_int(details.get("height")),
        date=raw.get("date_gmt") or raw.get("date"),
    )


def _mail(raw: Any) -> WordPressFormMail:
    if not isinstance(raw, dict):
        return WordPressFormMail()
    return WordPressFormMail(
        subject=str(raw.get("subject") or ""),
        sender=str(raw.get("sender") or ""),
        recipient=str(raw.get("recipient") or ""),
        body=str(raw.get("body") or ""),
        additional_headers=str(raw.get("additional_headers") or ""),
        attachments=str(raw.get("attachments") or ""),
        use_html=bool(raw.get("use_html")),
        exclude_blank=bool(raw.get("exclude_blank")),
        active=bool(raw.get("active", True)),
    )


def _form_row(raw: dict[str, Any]) -> WordPressFormRow:
    return WordPressFormRow(
        id=int(raw.get("id") or 0),
        slug=str(raw.get("slug") or ""),
        title=str(raw.get("title") or ""),
        locale=raw.get("locale") if isinstance(raw.get("locale"), str) else None,
    )


def _form_read(raw: dict[str, Any]) -> WordPressFormRead:
    """CF7's read: ``properties.form`` is ``{content, fields[]}``, ``additional_settings`` is
    ``{content, ...}``, the mails and messages are flat dicts."""
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    form = props.get("form")
    fields: list[str] = []
    if isinstance(form, dict):
        for tag in form.get("fields") or []:
            if not isinstance(tag, dict):
                continue
            name = tag.get("name")
            basetype = tag.get("basetype") or tag.get("type")
            if isinstance(name, str) and name and basetype != "submit":
                fields.append(name)
    settings = props.get("additional_settings")
    messages = props.get("messages")
    errors = raw.get("config_errors")
    return WordPressFormRead(
        **_form_row(raw).model_dump(),
        form=_text(form, "content"),
        fields=fields,
        mail=_mail(props.get("mail")),
        mail_2=_mail(props.get("mail_2")),
        messages={
            str(k): str(v) for k, v in messages.items() if isinstance(v, str)
        }
        if isinstance(messages, dict)
        else {},
        additional_settings=_text(settings, "content"),
        config_errors=errors if isinstance(errors, dict) else {},
    )


def _ability(raw: dict[str, Any]) -> WordPressAbility | None:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    annotations = meta.get("annotations") if isinstance(meta.get("annotations"), dict) else {}
    flags = {k: bool(v) for k, v in annotations.items() if isinstance(k, str)}
    input_schema = raw.get("input_schema")
    output_schema = raw.get("output_schema")
    return WordPressAbility(
        name=name,
        label=str(raw.get("label") or ""),
        description=str(raw.get("description") or ""),
        category=raw.get("category") if isinstance(raw.get("category"), str) else None,
        annotations=flags,
        readonly=bool(flags.get("readonly")),
        input_schema=input_schema if isinstance(input_schema, dict) else None,
        output_schema=output_schema if isinstance(output_schema, dict) else None,
    )


def _pick(raw: Any, *keys: str) -> str | None:
    """The first of ``keys`` present as a non-empty string, one level deep. The two core
    abilities' output shapes are documented loosely and have not met a live site from here,
    so the read is by candidate key and never by position."""
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int | float) and not isinstance(value, bool):
            return str(value)
    for value in raw.values():
        if isinstance(value, dict):
            found = _pick(value, *keys)
            if found:
                return found
    return None


class WordPressSurfaceService(WordPressService):
    """The routes under ``/wordpress/sites/{id}/…`` that reach into the site."""

    # --- plumbing ------------------------------------------------------------------------- #
    async def _open(self, site_id: uuid.UUID) -> tuple[WordPressSite, WordPressClient]:
        site = await self.repo.get_or_404(site_id)
        try:
            password = decrypt(site.app_password_encrypted)
        except ValueError as exc:
            raise AppError(
                "invalid_state", "errors.wordpress_secret_unreadable", status_code=409
            ) from exc
        return site, WordPressClient(site.base_url, site.username, password)

    async def _call(self, work: Callable[[], Awaitable[T]]) -> T:
        """One outbound call, connection released, the site's refusal mapped to ours.

        The status the site answered is the diagnosis: its 404 is a record (or a plugin) that
        is not there; its 400 is a body it would not take, which is a 422 here with the site's
        own words beside it; its 401/403 is a credential that reaches the site and not this
        route — kept apart from *our* 403, or an agent reads "you lack a schakl permission"
        over a WordPress role problem no schakl admin can fix.
        """
        async with self.ctx.release_db():
            try:
                return await work()
            except WordPressUnreachable as exc:
                raise AppError(
                    "upstream",
                    "errors.wordpress_unreachable",
                    status_code=502,
                    fields={"detail": describe_failure(exc)},
                ) from exc
            except WordPressAuthError as exc:
                raise AppError(
                    "upstream",
                    "errors.wordpress_site_refused",
                    status_code=502,
                    fields={"detail": describe_failure(exc)},
                ) from exc
            except WordPressError as exc:
                if exc.status == 404:
                    raise AppError(
                        "not_found",
                        "errors.wordpress_not_on_site",
                        status_code=404,
                        fields={"detail": describe_failure(exc)},
                    ) from exc
                if exc.status in (400, 422):
                    raise AppError(
                        "validation",
                        "errors.wordpress_site_rejected",
                        status_code=422,
                        fields={"detail": describe_failure(exc)},
                    ) from exc
                raise AppError(
                    "upstream",
                    "errors.wordpress_unavailable",
                    status_code=502,
                    fields={"detail": describe_failure(exc)},
                ) from exc

    async def _rest_base(self, client: WordPressClient, type_slug: str) -> str:
        if type_slug in _CORE_BASES:
            return _CORE_BASES[type_slug]
        types = await self._call(client.content_types)
        spec = types.get(type_slug)
        if not isinstance(spec, dict):
            raise AppError(
                "validation",
                "errors.wordpress_unknown_type",
                status_code=422,
                fields={"type": "errors.wordpress_unknown_type"},
                details={"known": sorted(types)},
            )
        base = spec.get("rest_base")
        return base if isinstance(base, str) and base else type_slug

    # --- the site ------------------------------------------------------------------------- #
    async def summary(self, site_id: uuid.UUID) -> WordPressSiteSummary:
        """What this site is. Three questions, each in its own ``try``: a site whose abilities
        are switched off still has post types, and one whose ``/types`` refuses an editor
        still has a name."""
        site, client = await self._open(site_id)
        index = await self._call(client.rest_index)
        namespaces = [n for n in index.get("namespaces") or [] if isinstance(n, str)]

        types: list[WordPressContentType] = []
        try:
            raw_types = await self._call(client.content_types)
        except AppError:
            raw_types = {}
        for slug, spec in raw_types.items():
            base = spec.get("rest_base")
            if not isinstance(base, str) or not base:
                continue
            types.append(
                WordPressContentType(
                    slug=slug,
                    name=str(spec.get("name") or slug),
                    rest_base=base,
                    hierarchical=bool(spec.get("hierarchical")),
                )
            )
        types.sort(key=lambda t: (t.slug not in _CORE_BASES, t.slug))

        wp_version = php_version = locale = timezone = None
        if _NS_ABILITIES in namespaces:
            readonly = {"readonly": True}
            try:
                env = await self._call(
                    lambda: client.run_ability(CORE_ENVIRONMENT_INFO, readonly)
                )
                wp_version = _pick(env, "wp_version", "wordpress_version", "version")
                php_version = _pick(env, "php_version", "php")
            except AppError:
                pass
            try:
                info = await self._call(lambda: client.run_ability(CORE_SITE_INFO, readonly))
                locale = _pick(info, "language", "locale")
                timezone = _pick(info, "timezone", "timezone_string")
            except AppError:
                pass

        return WordPressSiteSummary(
            site_id=site.id,
            base_url=site.base_url,
            name=index.get("name") if isinstance(index.get("name"), str) else None,
            description=(
                index.get("description") if isinstance(index.get("description"), str) else None
            ),
            wp_version=wp_version,
            php_version=php_version,
            locale=locale,
            timezone=timezone,
            content_types=types,
            namespaces=sorted(namespaces),
            has_forms=_NS_FORMS in namespaces,
            has_abilities=_NS_ABILITIES in namespaces,
            multilingual=_NS_WPML in namespaces,
        )

    # --- content -------------------------------------------------------------------------- #
    async def list_content(
        self,
        site_id: uuid.UUID,
        *,
        type_slug: str,
        search: str | None,
        status: str | None,
        lang: str | None,
        page: int,
        per_page: int,
    ) -> WordPressContentList:
        _, client = await self._open(site_id)
        base = await self._rest_base(client, type_slug)
        per_page = max(1, min(per_page, _MAX_PER_PAGE))
        params: dict[str, Any] = {
            "page": max(1, page),
            "per_page": per_page,
            "orderby": "modified",
            "order": "desc",
            # Every status the editor may see, or a list of published pages hides the draft
            # somebody asked about. Absent means all here because `context=edit` already
            # narrowed the audience to staff.
            "status": status or "publish,future,draft,pending,private",
        }
        if search:
            params["search"] = search
        if lang:
            params["lang"] = lang
        rows, total = await self._call(lambda: client.list_content(base, params))
        return WordPressContentList(
            items=[_content_row(type_slug, row) for row in rows],
            total=total,
            page=params["page"],
            per_page=per_page,
        )

    async def get_content(
        self, site_id: uuid.UUID, type_slug: str, wp_id: int
    ) -> WordPressContentRead:
        _, client = await self._open(site_id)
        base = await self._rest_base(client, type_slug)
        raw = await self._call(lambda: client.get_content(base, wp_id))
        return _content_read(type_slug, raw)

    async def create_content(
        self, site_id: uuid.UUID, data: WordPressContentCreate
    ) -> WordPressContentRead:
        if data.status in LIVE_STATUSES:
            self.ctx.require("wordpress.content.publish")
        site, client = await self._open(site_id)
        base = await self._rest_base(client, data.type)
        body = data.model_dump(exclude_none=True, exclude={"type"})
        raw = await self._call(lambda: client.write_content(base, None, body))
        record = _content_read(data.type, raw)
        await self.activity.record(
            ENTITY_TYPE,
            site.id,
            "content_created",
            {
                "type": data.type,
                "wp_id": record.id,
                "title": record.title,
                "status": record.status,
                "link": record.link,
            },
        )
        return record

    async def update_content(
        self,
        site_id: uuid.UUID,
        type_slug: str,
        wp_id: int,
        data: WordPressContentWrite,
    ) -> WordPressContentRead:
        """Change a record. The read that precedes the write is what decides the permission:
        a page that is live is a broadcast whatever field is touched."""
        body = data.model_dump(exclude_none=True)
        if not body:
            raise AppError("validation", "errors.nothing_to_update", status_code=422)
        site, client = await self._open(site_id)
        base = await self._rest_base(client, type_slug)
        before = _content_read(type_slug, await self._call(lambda: client.get_content(base, wp_id)))
        if before.status in LIVE_STATUSES or (data.status in LIVE_STATUSES):
            self.ctx.require("wordpress.content.publish")
        raw = await self._call(lambda: client.write_content(base, wp_id, body))
        record = _content_read(type_slug, raw)
        await self.activity.record(
            ENTITY_TYPE,
            site.id,
            "content_updated",
            {
                "type": type_slug,
                "wp_id": wp_id,
                "title": record.title,
                "status": record.status,
                "link": record.link,
                "fields": sorted(body),
            },
        )
        return record

    # --- media ---------------------------------------------------------------------------- #
    async def list_media(
        self, site_id: uuid.UUID, *, search: str | None, page: int, per_page: int
    ) -> WordPressMediaList:
        _, client = await self._open(site_id)
        per_page = max(1, min(per_page, _MAX_PER_PAGE))
        params: dict[str, Any] = {"page": max(1, page), "per_page": per_page}
        if search:
            params["search"] = search
        rows, total = await self._call(lambda: client.list_media(params))
        return WordPressMediaList(
            items=[_media_row(row) for row in rows],
            total=total,
            page=params["page"],
            per_page=per_page,
        )

    async def get_media(self, site_id: uuid.UUID, wp_id: int) -> WordPressMediaRow:
        _, client = await self._open(site_id)
        return _media_row(await self._call(lambda: client.get_media(wp_id)))

    # --- forms ---------------------------------------------------------------------------- #
    async def list_forms(self, site_id: uuid.UUID, *, search: str | None) -> list[WordPressFormRow]:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {"per_page": _MAX_PER_PAGE}
        if search:
            params["search"] = search
        rows = await self._call(lambda: client.list_forms(params))
        return [_form_row(row) for row in rows]

    async def get_form(self, site_id: uuid.UUID, wp_id: int) -> WordPressFormRead:
        _, client = await self._open(site_id)
        return _form_read(await self._call(lambda: client.get_form(wp_id)))

    async def create_form(self, site_id: uuid.UUID, data: WordPressFormCreate) -> WordPressFormRead:
        site, client = await self._open(site_id)
        body = data.model_dump(exclude_none=True)
        record = _form_read(await self._call(lambda: client.write_form(None, body)))
        await self.activity.record(
            ENTITY_TYPE, site.id, "form_created", {"wp_id": record.id, "title": record.title}
        )
        return record

    async def update_form(
        self, site_id: uuid.UUID, wp_id: int, data: WordPressFormWrite
    ) -> WordPressFormRead:
        body = data.model_dump(exclude_none=True)
        if not body:
            raise AppError("validation", "errors.nothing_to_update", status_code=422)
        site, client = await self._open(site_id)
        record = _form_read(await self._call(lambda: client.write_form(wp_id, body)))
        await self.activity.record(
            ENTITY_TYPE,
            site.id,
            "form_updated",
            {"wp_id": wp_id, "title": record.title, "fields": sorted(body)},
        )
        return record

    # --- abilities ------------------------------------------------------------------------ #
    async def abilities(self, site_id: uuid.UUID) -> list[WordPressAbility]:
        _, client = await self._open(site_id)
        rows = await self._call(client.abilities)
        out = [_ability(row) for row in rows]
        return sorted((a for a in out if a is not None), key=lambda a: a.name)

    async def run_ability(
        self, site_id: uuid.UUID, data: WordPressAbilityRun
    ) -> WordPressAbilityResult:
        """Run one ability by name.

        The registration is read first, for two reasons that are one: the verb core demands
        is the ability's own annotation, and so is whether ``ability.read`` suffices. An
        ability that does not call itself read-only is a write, whatever it does.
        """
        site, client = await self._open(site_id)
        spec = _ability(await self._call(lambda: client.ability(data.name)))
        if spec is None:
            raise AppError("not_found", "errors.wordpress_not_on_site", status_code=404)
        if not spec.readonly:
            self.ctx.require("wordpress.ability.run")
        output = await self._call(
            lambda: client.run_ability(spec.name, spec.annotations, data.input)
        )
        if not spec.readonly:
            await self.activity.record(
                ENTITY_TYPE, site.id, "ability_run", {"name": spec.name, "title": spec.label}
            )
        return WordPressAbilityResult(name=spec.name, readonly=spec.readonly, output=output)
