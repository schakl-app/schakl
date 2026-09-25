"""The schakl WordPress MCP Bridge plugin as a surface: ACF page builders, hidden post types,
media, WPML.

``surface.py`` reaches a site through what core's ``wp/v2`` offers, and docs/WORDPRESS.md §7
records where that stops: an ACF page builder is a repeater of typed rows with conditional
siblings, ACF's own REST layer validates without evaluating the conditions, a repeater can only
be re-posted whole, a post type without ``show_in_rest`` does not exist, and ``wp/v2/media``
takes multipart, which no MCP tool can send. The **schakl WordPress MCP Bridge** plugin (its
own repository, ``schakl-wordpress-mcp-bridge``) owns those four things on the site — the
schema, a compact reader, a validating writer with surgical ``ops``, and inline uploads — and
this file is how a connected site's plugin is reached under the credential schakl already holds.

Three rules beyond ``surface.py``'s four.

* **The plugin's refusal is carried, not translated.** A validation refusal arrives as
  ``details.problems`` — path, field, code, the allowed choices — and lands in schakl's
  envelope as ``details`` untouched: that is the machine-readable half §9 says ``fields``
  cannot be, and a sentence naming "blokken_blokken" without the row index would send an
  agent hunting.
* **"Not installed" is decided by the call, never by the stored version.** A bridge route on a
  site without the plugin answers core's ``rest_no_route`` and becomes a 409 that names the
  plugin; ``bridge_version`` on the row is what the panel prints and what a probe last saw,
  which may be a deploy stale.
* **The audience rule is read off the plugin's own record.** Editing a live page or setting a
  live status is ``content.publish`` here exactly as it is on the ``wp/v2`` routes, decided by
  reading the record first — the plugin's WordPress capabilities are a second gate, not a
  substitute for schakl's.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.errors import AppError
from app.integrations.wordpress.client import (
    WordPressAuthError,
    WordPressClient,
    WordPressError,
    WordPressUnreachable,
    describe_failure,
)
from app.integrations.wordpress.models import WordPressSite
from app.integrations.wordpress.schemas import (
    LIVE_STATUSES,
    WordPressBridgeDelete,
    WordPressBridgeForm,
    WordPressBridgeFormCreate,
    WordPressBridgeFormDelete,
    WordPressBridgeFormList,
    WordPressBridgeFormTranslate,
    WordPressBridgeFormUpdate,
    WordPressBridgeInfo,
    WordPressBridgeMedia,
    WordPressBridgeSchema,
    WordPressBridgeTerm,
    WordPressBridgeTermCreate,
    WordPressBridgeTermList,
    WordPressLanguages,
    WordPressMediaUpload,
    WordPressMenu,
    WordPressMenuCreate,
    WordPressMenuDeleted,
    WordPressMenuItemAdd,
    WordPressMenuItemUpdate,
    WordPressMenuList,
    WordPressMenuReorder,
    WordPressMenuUpdate,
    WordPressOptionsPages,
    WordPressOptionsRead,
    WordPressOptionsWrite,
    WordPressRecord,
    WordPressRecordCreate,
    WordPressRecordList,
    WordPressRecordUpdate,
    WordPressStringList,
    WordPressStringResult,
    WordPressStringUpdate,
    WordPressTranslationCreate,
    WordPressTranslations,
)
from app.integrations.wordpress.service import ENTITY_TYPE
from app.integrations.wordpress.surface import WordPressSurfaceService

#: The plugin's own slug, named in the 409 so an admin knows what to install.
BRIDGE_PLUGIN = "schakl-wordpress-mcp-bridge"

_MAX_PER_PAGE = 100


def _clean(data: Any) -> dict[str, Any]:
    """A request body for the plugin: the model minus what was not sent."""
    return data.model_dump(exclude_none=True, by_alias=True)


class WordPressBridgeService(WordPressSurfaceService):
    """The routes under ``/wordpress/sites/{id}/bridge/…``."""

    # --- plumbing ------------------------------------------------------------------------- #
    async def _bridge(
        self,
        client: WordPressClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """One plugin call, connection released, the plugin's refusal mapped to ours.

        The plugin's statuses are its diagnosis: 404 with core's ``rest_no_route`` is the
        plugin not being there at all; any other 404 is a record; 400/413/422 is a body it
        would not take, with ``details`` carried whole; 409 is a capability the site lacks
        (WPML, ACF) or a state that refuses (a translation that exists), named in ``details``.
        """
        async with self.ctx.release_db():
            try:
                return await client.bridge(method, path, params=params, json=json)
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
                    details={**exc.details, "code": exc.code},
                ) from exc
            except WordPressError as exc:
                if exc.status == 404 and exc.code == "rest_no_route":
                    raise AppError(
                        "invalid_state",
                        "errors.wordpress_bridge_missing",
                        status_code=409,
                        fields={"detail": describe_failure(exc)},
                        details={"plugin": BRIDGE_PLUGIN, "base_url": client.base_url},
                    ) from exc
                if exc.status == 404:
                    raise AppError(
                        "not_found",
                        "errors.wordpress_not_on_site",
                        status_code=404,
                        fields={"detail": describe_failure(exc)},
                        details={**exc.details, "code": exc.code},
                    ) from exc
                if exc.status in (400, 413, 422):
                    raise AppError(
                        "validation",
                        "errors.wordpress_bridge_rejected",
                        status_code=422,
                        fields={"detail": describe_failure(exc)},
                        details={**exc.details, "code": exc.code},
                    ) from exc
                if exc.status == 409:
                    raise AppError(
                        "invalid_state",
                        "errors.wordpress_bridge_unavailable",
                        status_code=409,
                        fields={"detail": describe_failure(exc)},
                        details={**exc.details, "code": exc.code},
                    ) from exc
                raise AppError(
                    "upstream",
                    "errors.wordpress_unavailable",
                    status_code=502,
                    fields={"detail": describe_failure(exc)},
                ) from exc

    async def _trail(self, site: WordPressSite, action: str, payload: dict[str, Any]) -> None:
        await self.activity.record(ENTITY_TYPE, site.id, action, payload)

    # --- the site ------------------------------------------------------------------------- #
    async def info(self, site_id: uuid.UUID) -> WordPressBridgeInfo:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "GET", "/info")
        body = body if isinstance(body, dict) else {}
        plugin = body.get("plugin") if isinstance(body.get("plugin"), dict) else {}
        version = plugin.get("version")
        # The call is the observation: a site that just answered has the plugin at *this*
        # version, whatever the last probe recorded.
        if isinstance(version, str) and version != site.bridge_version:
            site.bridge_version = version
            await self.ctx.session.flush()
        return WordPressBridgeInfo(
            site_id=site.id,
            base_url=site.base_url,
            bridge_version=version if isinstance(version, str) else site.bridge_version,
            **{
                k: v
                for k, v in body.items()
                if k not in ("site_id", "base_url", "bridge_version")
            },
        )

    async def schema(
        self,
        site_id: uuid.UUID,
        *,
        post_type: str | None,
        template: str | None,
        parent: int | None,
        wp_id: int | None,
        options_page: str | None,
        taxonomy: str | None,
    ) -> WordPressBridgeSchema:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {}
        if wp_id:
            params["id"] = wp_id
        elif options_page:
            params["options_page"] = options_page
        elif taxonomy:
            params["taxonomy"] = taxonomy
        elif post_type:
            params["post_type"] = post_type
            if template:
                params["template"] = template
            if parent:
                params["parent"] = parent
        else:
            raise AppError(
                "validation",
                "errors.wordpress_bridge_schema_subject",
                status_code=422,
                fields={"post_type": "errors.wordpress_bridge_schema_subject"},
            )
        body = await self._bridge(client, "GET", "/schema", params=params)
        return WordPressBridgeSchema(**(body if isinstance(body, dict) else {}))

    # --- records -------------------------------------------------------------------------- #
    async def list_records(
        self,
        site_id: uuid.UUID,
        *,
        post_type: str,
        search: str | None,
        status: str | None,
        lang: str | None,
        parent: int | None,
        page: int,
        per_page: int,
    ) -> WordPressRecordList:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {
            "post_type": post_type,
            "page": max(1, page),
            "per_page": max(1, min(per_page, _MAX_PER_PAGE)),
        }
        if search:
            params["search"] = search
        if status:
            params["status"] = status
        if lang:
            params["lang"] = lang
        if parent is not None:
            params["parent"] = parent
        body = await self._bridge(client, "GET", "/content", params=params)
        return WordPressRecordList(
            **(body if isinstance(body, dict) else {"post_type": post_type})
        )

    async def get_record(
        self, site_id: uuid.UUID, wp_id: int, *, mode: str, include_schema: bool
    ) -> WordPressRecord:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {"fields": mode}
        if include_schema:
            params["include_schema"] = "true"
        body = await self._bridge(client, "GET", f"/content/{wp_id}", params=params)
        return WordPressRecord(**(body if isinstance(body, dict) else {}))

    async def create_record(
        self, site_id: uuid.UUID, data: WordPressRecordCreate
    ) -> WordPressRecord:
        if data.status in LIVE_STATUSES:
            self.ctx.require("wordpress.content.publish")
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", "/content", json=_clean(data))
        record = WordPressRecord(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "content_created",
            {
                "type": record.post_type,
                "wp_id": record.id,
                "title": record.title,
                "status": record.status,
                "link": record.link,
                "via": BRIDGE_PLUGIN,
            },
        )
        return record

    async def update_record(
        self, site_id: uuid.UUID, wp_id: int, data: WordPressRecordUpdate
    ) -> WordPressRecord:
        """The read that precedes the write decides the permission (surface.py's rule)."""
        payload = _clean(data)
        if not payload:
            raise AppError("validation", "errors.nothing_to_update", status_code=422)
        site, client = await self._open(site_id)
        before = await self._bridge(client, "GET", f"/content/{wp_id}", params={"fields": "none"})
        before_status = before.get("status") if isinstance(before, dict) else None
        if before_status in LIVE_STATUSES or data.status in LIVE_STATUSES:
            self.ctx.require("wordpress.content.publish")
        body = await self._bridge(client, "PATCH", f"/content/{wp_id}", json=payload)
        record = WordPressRecord(**(body if isinstance(body, dict) else {}))
        touched = sorted(k for k in payload if k not in ("fields", "ops", "mode"))
        if data.fields:
            touched.extend(f"fields.{name}" for name in sorted(data.fields))
        if data.ops:
            touched.append(f"ops×{len(data.ops)}")
        await self._trail(
            site,
            "content_updated",
            {
                "type": record.post_type,
                "wp_id": wp_id,
                "title": record.title,
                "status": record.status,
                "link": record.link,
                "fields": touched,
                "via": BRIDGE_PLUGIN,
            },
        )
        return record

    async def delete_record(
        self, site_id: uuid.UUID, wp_id: int, *, force: bool
    ) -> WordPressBridgeDelete:
        site, client = await self._open(site_id)
        before = await self._bridge(client, "GET", f"/content/{wp_id}", params={"fields": "none"})
        body = await self._bridge(client, "DELETE", f"/content/{wp_id}", json={"force": force})
        result = WordPressBridgeDelete(**(body if isinstance(body, dict) else {"id": wp_id}))
        await self._trail(
            site,
            "content_deleted",
            {
                "type": before.get("post_type") if isinstance(before, dict) else None,
                "wp_id": wp_id,
                "title": before.get("title") if isinstance(before, dict) else None,
                "permanent": bool(result.deleted),
            },
        )
        return result

    # --- media ---------------------------------------------------------------------------- #
    async def upload_media(
        self, site_id: uuid.UUID, data: WordPressMediaUpload
    ) -> WordPressBridgeMedia:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", "/media", json=_clean(data))
        media = WordPressBridgeMedia(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "media_uploaded",
            {
                "wp_id": media.id,
                "title": media.title or (data.filename or data.url or ""),
                "url": media.url,
                "attach_to": data.attach_to,
            },
        )
        return media

    # --- terms ---------------------------------------------------------------------------- #
    async def list_terms(
        self,
        site_id: uuid.UUID,
        *,
        taxonomy: str,
        search: str | None,
        lang: str | None,
        mode: str,
    ) -> WordPressBridgeTermList:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {"taxonomy": taxonomy, "fields": mode, "per_page": 200}
        if search:
            params["search"] = search
        if lang:
            params["lang"] = lang
        body = await self._bridge(client, "GET", "/terms", params=params)
        return WordPressBridgeTermList(
            **(body if isinstance(body, dict) else {"taxonomy": taxonomy})
        )

    async def create_term(
        self, site_id: uuid.UUID, data: WordPressBridgeTermCreate
    ) -> WordPressBridgeTerm:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", "/terms", json=_clean(data))
        term = WordPressBridgeTerm(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "term_created",
            {"taxonomy": term.taxonomy, "wp_id": term.id, "title": term.name},
        )
        return term

    # --- options -------------------------------------------------------------------------- #
    async def options_pages(self, site_id: uuid.UUID) -> WordPressOptionsPages:
        _, client = await self._open(site_id)
        body = await self._bridge(client, "GET", "/options")
        return WordPressOptionsPages(**(body if isinstance(body, dict) else {}))

    async def options_get(
        self, site_id: uuid.UUID, page: str, *, lang: str | None, mode: str, include_schema: bool
    ) -> WordPressOptionsRead:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {"fields": mode}
        if lang:
            params["lang"] = lang
        if include_schema:
            params["include_schema"] = "true"
        body = await self._bridge(client, "GET", f"/options/{page}", params=params)
        return WordPressOptionsRead(**(body if isinstance(body, dict) else {"page": page}))

    async def options_update(
        self, site_id: uuid.UUID, page: str, data: WordPressOptionsWrite
    ) -> WordPressOptionsRead:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "PATCH", f"/options/{page}", json=_clean(data))
        result = WordPressOptionsRead(**(body if isinstance(body, dict) else {"page": page}))
        await self._trail(
            site,
            "options_updated",
            {
                "page": page,
                "title": result.title or page,
                "fields": sorted(data.fields or {})
                + ([f"ops×{len(data.ops)}"] if data.ops else []),
                "lang": data.lang,
            },
        )
        return result

    # --- menus ---------------------------------------------------------------------------- #
    async def menus(self, site_id: uuid.UUID, *, lang: str | None) -> WordPressMenuList:
        _, client = await self._open(site_id)
        body = await self._bridge(
            client, "GET", "/menus", params={"lang": lang} if lang else None
        )
        return WordPressMenuList(**(body if isinstance(body, dict) else {}))

    async def menu(self, site_id: uuid.UUID, menu: str) -> WordPressMenu:
        _, client = await self._open(site_id)
        body = await self._bridge(client, "GET", f"/menus/{menu}")
        return WordPressMenu(**(body if isinstance(body, dict) else {}))

    async def menu_add_item(
        self, site_id: uuid.UUID, menu: str, data: WordPressMenuItemAdd
    ) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", f"/menus/{menu}/items", json=_clean(data))
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_updated",
            {
                "menu": result.slug or menu,
                "title": result.name,
                "added": data.title or data.url or data.object_id,
            },
        )
        return result

    async def menu_remove_item(self, site_id: uuid.UUID, menu: str, item_id: int) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "DELETE", f"/menus/{menu}/items/{item_id}")
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_updated",
            {"menu": result.slug or menu, "title": result.name, "removed": item_id},
        )
        return result

    async def menu_update_item(
        self, site_id: uuid.UUID, menu: str, item_id: int, data: WordPressMenuItemUpdate
    ) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(
            client, "PATCH", f"/menus/{menu}/items/{item_id}", json=_clean(data)
        )
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_updated",
            {
                "menu": result.slug or menu,
                "title": result.name,
                "updated": item_id,
                "fields": sorted(_clean(data)),
            },
        )
        return result

    async def menu_reorder(
        self, site_id: uuid.UUID, menu: str, data: WordPressMenuReorder
    ) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "PUT", f"/menus/{menu}/order", json=_clean(data))
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_updated",
            {
                "menu": result.slug or menu,
                "title": result.name,
                "reordered": len(data.order),
                "parent": data.parent or None,
            },
        )
        return result

    async def menu_create(self, site_id: uuid.UUID, data: WordPressMenuCreate) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", "/menus", json=_clean(data))
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_created",
            {"menu": result.slug, "title": result.name, "locations": data.locations or []},
        )
        return result

    async def menu_update(
        self, site_id: uuid.UUID, menu: str, data: WordPressMenuUpdate
    ) -> WordPressMenu:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "PATCH", f"/menus/{menu}", json=_clean(data))
        result = WordPressMenu(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_updated",
            {"menu": result.slug or menu, "title": result.name, "fields": sorted(_clean(data))},
        )
        return result

    async def menu_delete(self, site_id: uuid.UUID, menu: str) -> WordPressMenuDeleted:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "DELETE", f"/menus/{menu}")
        result = WordPressMenuDeleted(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "menu_deleted",
            {"menu": result.slug, "title": result.name, "items_removed": result.items_removed},
        )
        return result

    # --- WPML ----------------------------------------------------------------------------- #
    async def languages(self, site_id: uuid.UUID) -> WordPressLanguages:
        _, client = await self._open(site_id)
        body = await self._bridge(client, "GET", "/wpml/languages")
        return WordPressLanguages(**(body if isinstance(body, dict) else {}))

    async def translations(self, site_id: uuid.UUID, wp_id: int) -> WordPressTranslations:
        _, client = await self._open(site_id)
        body = await self._bridge(client, "GET", f"/wpml/translations/{wp_id}")
        return WordPressTranslations(**(body if isinstance(body, dict) else {}))

    async def translate(
        self, site_id: uuid.UUID, wp_id: int, data: WordPressTranslationCreate
    ) -> WordPressRecord | WordPressTranslations:
        """Create a linked translation, or — with ``translation_id`` — connect an existing one."""
        site, client = await self._open(site_id)
        if data.translation_id:
            body = await self._bridge(
                client,
                "POST",
                f"/wpml/translations/{wp_id}/connect",
                json={"translation_id": data.translation_id, "lang": data.lang},
            )
            result = WordPressTranslations(**(body if isinstance(body, dict) else {}))
            await self._trail(
                site,
                "translation_created",
                {"wp_id": wp_id, "lang": data.lang, "connected": data.translation_id, "title": ""},
            )
            return result
        if data.status in LIVE_STATUSES:
            self.ctx.require("wordpress.content.publish")
        payload = _clean(data)
        payload.pop("translation_id", None)
        body = await self._bridge(client, "POST", f"/wpml/translations/{wp_id}", json=payload)
        record = WordPressRecord(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "translation_created",
            {
                "wp_id": wp_id,
                "lang": data.lang,
                "translation": record.id,
                "title": record.title,
                "status": record.status,
                "link": record.link,
            },
        )
        return record

    async def strings(
        self,
        site_id: uuid.UUID,
        *,
        domain: str | None,
        search: str | None,
        page: int,
        per_page: int,
    ) -> WordPressStringList:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {"page": max(1, page), "per_page": max(1, min(per_page, 200))}
        if domain:
            params["domain"] = domain
        if search:
            params["search"] = search
        body = await self._bridge(client, "GET", "/wpml/strings", params=params)
        return WordPressStringList(**(body if isinstance(body, dict) else {}))

    async def string_update(
        self, site_id: uuid.UUID, data: WordPressStringUpdate
    ) -> WordPressStringResult:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "PUT", "/wpml/strings", json=_clean(data))
        result = WordPressStringResult(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "string_translated",
            {"string_id": result.id, "lang": result.lang, "title": data.name or str(result.id)},
        )
        return result

    # --- Contact Form 7 ------------------------------------------------------------------- #
    async def list_forms(
        self,
        site_id: uuid.UUID,
        *,
        search: str | None,
        lang: str | None,
        page: int,
        per_page: int,
    ) -> WordPressBridgeFormList:
        _, client = await self._open(site_id)
        params: dict[str, Any] = {
            "page": max(1, page),
            "per_page": max(1, min(per_page, _MAX_PER_PAGE)),
        }
        if search:
            params["search"] = search
        if lang:
            params["lang"] = lang
        body = await self._bridge(client, "GET", "/forms", params=params)
        return WordPressBridgeFormList(**(body if isinstance(body, dict) else {}))

    async def get_form(self, site_id: uuid.UUID, wp_id: int) -> WordPressBridgeForm:
        _, client = await self._open(site_id)
        body = await self._bridge(client, "GET", f"/forms/{wp_id}")
        return WordPressBridgeForm(**(body if isinstance(body, dict) else {}))

    async def create_form(
        self, site_id: uuid.UUID, data: WordPressBridgeFormCreate
    ) -> WordPressBridgeForm:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", "/forms", json=_clean(data))
        form = WordPressBridgeForm(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "form_created",
            {"wp_id": form.id, "title": form.title, "lang": form.lang, "via": BRIDGE_PLUGIN},
        )
        return form

    async def update_form(
        self, site_id: uuid.UUID, wp_id: int, data: WordPressBridgeFormUpdate
    ) -> WordPressBridgeForm:
        payload = _clean(data)
        if not payload:
            raise AppError("validation", "errors.nothing_to_update", status_code=422)
        site, client = await self._open(site_id)
        body = await self._bridge(client, "PATCH", f"/forms/{wp_id}", json=payload)
        form = WordPressBridgeForm(**(body if isinstance(body, dict) else {}))
        touched = sorted(k for k in payload if k != "strings")
        if data.strings:
            touched.extend(f"strings.{lang}" for lang in sorted(data.strings))
        await self._trail(
            site,
            "form_updated",
            {"wp_id": wp_id, "title": form.title, "fields": touched, "via": BRIDGE_PLUGIN},
        )
        return form

    async def delete_form(self, site_id: uuid.UUID, wp_id: int) -> WordPressBridgeFormDelete:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "DELETE", f"/forms/{wp_id}")
        result = WordPressBridgeFormDelete(**(body if isinstance(body, dict) else {"id": wp_id}))
        await self._trail(
            site,
            "content_deleted",
            {
                "type": "wpcf7_contact_form",
                "wp_id": wp_id,
                "title": result.title,
                "permanent": True,
            },
        )
        return result

    async def translate_form(
        self, site_id: uuid.UUID, wp_id: int, data: WordPressBridgeFormTranslate
    ) -> WordPressBridgeForm:
        site, client = await self._open(site_id)
        body = await self._bridge(client, "POST", f"/forms/{wp_id}/translate", json=_clean(data))
        form = WordPressBridgeForm(**(body if isinstance(body, dict) else {}))
        await self._trail(
            site,
            "translation_created",
            {
                "wp_id": wp_id,
                "lang": data.lang,
                "translation": form.id,
                "connected": data.translation_id,
                "title": form.title,
                "link": form.shortcode,
            },
        )
        return form
