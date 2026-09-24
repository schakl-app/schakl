"""A scriptable stand-in for a WordPress site with Rank Math (docs/WORDPRESS.md).

The wordpress module must never touch the network in tests, and its interesting behaviour is
almost entirely *degradation*: a credential that reaches core REST but not Rank Math, a site
without the MCP Adapter, a user who is not an administrator, a host that eats the
``Authorization`` header. That needs a WordPress that can be told to be each of those, not a
pile of one-off stubs.

**The fake rejects a bad credential everywhere.** That is not politeness, it is the whole
reason the fake can catch anything: a stand-in kinder than the real server is a stand-in the bug
hides in, and the specific bug this guards against is a probe that concludes "the token is
fine" from an endpoint it never authenticated against. Every route below checks the Basic
header first, and the four toggles turn *individual surfaces* off without ever making a wrong
password work.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.integrations.wordpress.client import supports_ai_visibility

#: The plugin file wordpress.org fixes for Rank Math, and the version that first shipped AI
#: Visibility. A fake on 1.0.272 is how a test says "installed, but too old".
RANKMATH_PLUGIN = "seo-by-rank-math/rank-math.php"
RANKMATH_VERSION = "1.0.275"


def _json(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _wp_error(code: str, message: str, status: int) -> httpx.Response:
    """WordPress's own error envelope. The ``code`` is what a probe stores and reasons about —
    ``rest_no_route`` (the plugin is not here) and ``rest_forbidden`` (you may not call it) are
    opposite diagnoses that share a 4xx, and a fake that returned a bare status could not tell
    them apart either."""
    return httpx.Response(
        status, json={"code": code, "message": message, "data": {"status": status}}
    )


class FakeWordPress:
    """One WordPress site. Toggles turn surfaces off; none of them makes a bad password work."""

    def __init__(
        self,
        *,
        username: str = "agency",
        app_password: str = "abcd EFGH ijkl MNOP qrst UVWX",
    ) -> None:
        self.username = username
        self.app_password = app_password

        # --- what this site has ---------------------------------------------------------- #
        #: WordPress 6.9 core registers `wp-abilities/v1`; an older site does not.
        self.has_abilities = True
        #: The MCP Adapter plugin: namespace `mcp`, one **route** per server under it. The
        #: fake deliberately serves a non-default server name, so a probe that assumed
        #: `mcp-adapter-default-server` fails — and it puts the namespace in the index exactly
        #: as the real adapter does (bare `mcp`), because the first probe matched `mcp/` and
        #: recorded every live site as having no adapter (docs/WORDPRESS.md §4).
        self.mcp_server: str | None = "agency-server"
        #: Rank Math's version, or None for "not installed at all".
        self.rankmath_version: str | None = RANKMATH_VERSION
        #: Rank Math is installed but its account is not connected to a Content AI
        #: subscription — a real state that answers 401 `aiv_unauthorized` on every AI
        #: Visibility route while the rest of the site is perfectly healthy.
        self.aiv_subscribed = True
        #: Whether this credential is a WordPress administrator. Every Rank Math AI Visibility
        #: route is `manage_options`, so an editor's password reaches `wp/v2` and nothing else.
        self.is_admin = True
        #: A host that strips the `Authorization` header — a bare 401 on a correct password,
        #: and one of the two failures that are otherwise indistinguishable from a revoked one.
        self.strips_auth_header = False
        #: Not a WordPress site at all (or REST disabled): everything 404s.
        self.is_wordpress = True
        #: Contact Form 7 installed.
        self.has_forms = True
        #: WPML installed: records carry a `lang`, and `?lang=` filters.
        self.multilingual = False

        #: Content, keyed by rest base then id — pages and posts plus one custom post type
        #: (`dienst`, on the base `diensten`) so the type lookup is exercised. ACF field
        #: values ride on `acf`, as ACF exposes them with "Show in REST API" on.
        self.content: dict[str, dict[int, dict]] = {
            "pages": {
                1: {
                    "id": 1, "type": "page", "slug": "home", "status": "publish",
                    "link": "https://klant.nl/", "title": "Home",
                    "content": "<h2>Welkom</h2>", "excerpt": "",
                    "modified_gmt": "2026-09-01T10:00:00", "parent": 0, "template": "",
                    "acf": {"subtitel": "Industriële testen", "slider": [{"titel": "Een"}]},
                    "meta": {"_acf_changed": False}, "featured_media": 6632, "lang": "nl",
                },
                2: {
                    "id": 2, "type": "page", "slug": "nieuwe-dienst", "status": "draft",
                    "link": "https://klant.nl/?page_id=2", "title": "Nieuwe dienst",
                    "content": "", "excerpt": "", "modified_gmt": "2026-09-02T10:00:00",
                    "parent": 0, "template": "", "acf": {"subtitel": ""}, "meta": {},
                    "featured_media": 0, "lang": "nl",
                },
            },
            "posts": {
                10: {
                    "id": 10, "type": "post", "slug": "nieuws", "status": "publish",
                    "link": "https://klant.nl/nieuws/", "title": "Nieuws",
                    "content": "<p>Hallo</p>", "excerpt": "", "modified_gmt": "2026-08-01T10:00:00",
                    "parent": 0, "acf": None, "meta": {}, "featured_media": 0, "lang": "nl",
                }
            },
            "diensten": {},
        }
        self.media: dict[int, dict] = {
            6632: {
                "id": 6632, "title": {"rendered": "hero.jpg"}, "mime_type": "image/jpeg",
                "source_url": "https://klant.nl/wp-content/uploads/hero.jpg",
                "alt_text": "Hero", "media_details": {"width": 1600, "height": 900},
                "date_gmt": "2026-01-01T00:00:00",
            }
        }
        self.forms: dict[int, dict] = {
            6584: {
                "id": 6584, "slug": "contactformulier", "title": "Contactformulier",
                "locale": "nl_NL",
                "form": "[text* your-name] [email* your-email] [submit \"Verstuur\"]",
                "mail": {"subject": "Nieuw bericht", "sender": "site@klant.nl",
                         "recipient": "info@klant.nl", "body": "[your-name]",
                         "additional_headers": "", "attachments": "", "use_html": False,
                         "exclude_blank": False, "active": True},
                "mail_2": {"subject": "", "sender": "", "recipient": "", "body": "",
                           "additional_headers": "", "attachments": "", "use_html": False,
                           "exclude_blank": False, "active": False},
                "messages": {"mail_sent_ok": "Bedankt."},
                "additional_settings": "",
            }
        }
        #: Every write the fake received, so a test can assert the body a route sent.
        self.writes: list[tuple[str, dict]] = []

        # --- the schakl WordPress MCP Bridge plugin (docs/WORDPRESS.md §9) --------------- #
        #: Installed and answering `schakl/v1`. Off, every bridge route is core's
        #: `rest_no_route` — the exact answer a site without the plugin gives.
        self.has_bridge = True
        self.bridge_version = "1.0.0"
        #: Records the plugin serves, keyed by id, in its own canonical shape: `fields` is the
        #: name-keyed ACF tree (compact), `references` what the ids resolve to. One page in the
        #: shape of the themes' page builder (a `blokken_blokken` repeater of typed rows).
        self.bridge_records: dict[int, dict] = {
            8262: {
                "id": 8262, "post_type": "page", "title": "Arbeidsongeschiktheidsverzekering",
                "slug": "aov", "status": "publish", "link": "https://klant.nl/aov/",
                "modified": "2026-09-21T12:20:14+02:00", "parent": 7813,
                "path": ["Particulier", "Verzekeringen"], "content": "", "excerpt": "",
                "taxonomies": {}, "seo": {"provider": "rank_math", "title": "AOV | Klant"},
                "featured_media": None, "lang": "nl",
                "fields": {
                    "kleur": "auto",
                    "blokken_blokken": [
                        {"type": "10", "achtergrond": "0", "afbeelding_of_video": "0",
                         "afbeelding_0_1": 7793, "titel_0_1_2": "Eerst het risico begrijpen"},
                        {"type": "15", "achtergrond": "0",
                         "werkwijzeblok": {"titel": "Zo werken wij", "stappen": [
                             {"titel": "We brengen je situatie in kaart"}]}},
                    ],
                },
                "references": {"attachments": {"7793": {
                    "id": 7793, "url": "https://klant.nl/wp-content/uploads/aov.jpg",
                    "alt": "Adviesgesprek", "title": "aov", "mime": "image/jpeg",
                    "width": 1600, "height": 900}}},
            },
            7813: {
                "id": 7813, "post_type": "page", "title": "Verzekeringen", "slug": "verzekeringen",
                "status": "publish", "link": "https://klant.nl/verzekeringen/",
                "modified": "2026-06-22T14:27:07+02:00", "parent": 7809, "path": ["Particulier"],
                "content": "", "excerpt": "", "taxonomies": {}, "seo": None,
                "featured_media": None, "lang": "nl", "fields": {"kleur": "green"},
                "references": {},
            },
            9001: {
                "id": 9001, "post_type": "dienst", "title": "Hypotheekadvies", "slug": "hypotheek",
                "status": "draft", "link": "https://klant.nl/?p=9001",
                "modified": "2026-09-22T09:00:00+02:00", "parent": None, "path": [],
                "content": "", "excerpt": "", "taxonomies": {}, "seo": None,
                "featured_media": None, "lang": "nl", "fields": {"intro": "Intro"},
                "references": {},
            },
        }
        #: The one ACF schema the fake describes: `kleur` (a select with three choices) and
        #: the page builder, whose rows carry a `type` and a title shown only for type 10.
        self.bridge_choices = {"kleur": ["auto", "green", "blue"]}
        self.bridge_media: dict[int, dict] = {}
        self.bridge_terms: dict[int, dict] = {
            144: {"id": 144, "taxonomy": "faq_categories", "name": "AOV", "slug": "aov",
                  "parent": None, "lang": "nl"},
        }
        self.bridge_options: dict[str, dict] = {
            "bedrijfsinformatie": {"telefoon": "0113-123456", "partners": []},
        }
        self.bridge_menu_items: list[dict] = [
            {"id": 501, "title": "Home", "url": "https://klant.nl/", "type": "post_type",
             "object": "page", "object_id": 1, "parent": None, "order": 1, "children": []},
        ]
        self.bridge_strings: dict[int, dict] = {
            7: {"id": 7, "domain": "theme", "name": "Lees meer", "value": "Lees meer",
                "lang": "nl", "translations": {}},
        }
        #: Contact Form 7 forms the plugin serves (`forms.*`, bridge 1.2.0), keyed by id, in
        #: the plugin's own record shape. Off `has_forms`, every forms route is the plugin's
        #: 409 `unavailable`. `forms_translatable` is WPML listing the post type as translatable.
        self.bridge_forms: dict[int, dict] = {
            6584: {
                "id": 6584, "title": "Contactformulier", "slug": "contactformulier",
                "hash": "a1b2c3d", "locale": "nl_NL",
                "form": "[text* your-name] [email* your-email] [submit \"Verstuur\"]",
                "mail": {"active": True, "subject": "Nieuw bericht", "sender": "site@klant.nl",
                         "recipient": "info@klant.nl", "body": "[your-name]",
                         "additional_headers": "", "attachments": "", "use_html": False,
                         "exclude_blank": False},
                "mail_2": {"active": False, "subject": "", "sender": "", "recipient": "",
                           "body": "", "additional_headers": "", "attachments": "",
                           "use_html": False, "exclude_blank": False},
                "messages": {"mail_sent_ok": "Bedankt.", "validation_error": "Controleer."},
                "additional_settings": "", "lang": "nl",
            }
        }
        self.forms_translatable = False
        #: Strings the WPML Contact Form 7 Multilingual add-on would register for form 6584.
        self.bridge_form_strings: dict[int, dict] = {
            301: {"id": 301, "package": 12, "name": "Form", "title": "Form", "lang": "nl",
                  "value": "[text* your-name]", "translations": {}},
        }
        #: Every bridge call, `(method, subpath, body)`, so a test can assert what was sent.
        self.bridge_calls: list[tuple[str, str, object]] = []
        #: Abilities beyond Rank Math's: one read-only, one write. `acf/field-groups` is what
        #: ACF 6.8 registers; the write one stands in for `acf/create-field-group`.
        self.extra_abilities: list[dict] = [
            {
                "name": "core/get-environment-info", "label": "Environment",
                "description": "", "category": "site",
                "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "core/get-site-info", "label": "Site", "description": "",
                "category": "site", "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "acf/field-groups", "label": "Field groups", "description": "",
                "category": "acf", "input_schema": {}, "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": True}},
            },
            {
                "name": "acf/create-field-group", "label": "Create field group",
                "description": "", "category": "acf",
                "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
                "output_schema": {},
                "meta": {"show_in_rest": True, "annotations": {"readonly": False}},
            },
        ]
        self.ability_runs: list[tuple[str, str, object]] = []
        #: Plugin routes the curated surface knows nothing about, for the passthrough:
        #: ``(method, path) -> body``. `wp/v2/settings` stands in for the takeover routes.
        self.extra_routes: dict[tuple[str, str], object] = {
            ("GET", "/wp-json/wpml/v1/languages"): [{"code": "nl"}, {"code": "en"}],
            ("POST", "/wp-json/litespeed/v1/purge"): {"purged": True},
            ("GET", "/wp-json/wp/v2/settings"): {"title": "Klant BV", "email": "info@klant.nl"},
            ("POST", "/wp-json/wp/v2/settings"): {"title": "Overgenomen"},
            ("POST", "/wp-json/wp/v2/users"): {"id": 99, "roles": ["administrator"]},
            ("GET", "/wp-json/wp/v2/users"): [{"id": 1, "name": "Agency"}],
            ("GET", "/wp-json/big/v1/rows"): [{"n": i, "pad": "x" * 2000} for i in range(400)],
        }

        #: Rank Math brands, in the `/overview` row shape the plugin's `map_overview_brand()`
        #: produces.
        self.brands: list[dict] = [
            {
                "id": "brand-1",
                "name": "Klant BV",
                "url": "https://klant.nl",
                "locale": "NL",
                "status": "active",
                "score": 42.5,
                "rank": 3,
                # 0-100 (docs/WORDPRESS.md §3), the scale the plugin's own badge renders.
                "avg_sentiment": 62.0,
                "mentions": 18,
                "citations": 7,
                "last_analyzed": "2026-08-10T04:00:00Z",
                "analysis_status": "success",
            }
        ]
        self.summary: dict = {"tracked_brands": 1}

        #: Every path this fake was asked for, in order — so a test can assert that a probe
        #: really did ask each surface rather than inferring one from another.
        self.calls: list[str] = []
        #: How many times `/overview` was asked to bypass Rank Math's 12-hour cache. The one
        #: assertion that catches a sync built on the *ability* instead of the REST route.
        self.refresh_calls = 0

    # --- auth ---------------------------------------------------------------------------- #
    def _authorised(self, request: httpx.Request) -> bool:
        if self.strips_auth_header:
            return False
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:]).decode()
        except Exception:  # noqa: BLE001
            return False
        user, _, password = raw.partition(":")
        return user == self.username and password == self.app_password

    # --- transport ----------------------------------------------------------------------- #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)

        if not self.is_wordpress:
            return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

        # Checked before anything else, on every route. A fake that authenticated only the
        # routes it felt were sensitive would let a half-probed credential pass.
        if not self._authorised(request):
            return _wp_error(
                "rest_not_logged_in", "You are not currently logged in.", 401
            )

        if path.startswith("/wp-json/schakl/v1"):
            if not self.has_bridge:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return self._bridge_route(request, path[len("/wp-json/schakl/v1"):])

        if (request.method, path) in self.extra_routes:
            if request.method != "GET":
                import json as _json_mod

                self.writes.append((path, _json_mod.loads(request.content or b"null")))
            return _json(self.extra_routes[(request.method, path)])
        if path in ("/wp-json", "/wp-json/"):
            return _json(self._index())
        if path == "/wp-json/wp/v2/users/me":
            return _json(self._me())
        if path.startswith("/wp-json/wp-abilities/v1/abilities"):
            if not self.has_abilities:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return self._abilities_route(request, path)
        if path == "/wp-json/wp/v2/types":
            return _json(self._types())
        if path.startswith("/wp-json/wp/v2/media"):
            return self._media_route(request, path)
        if path.startswith("/wp-json/wp/v2/") and path.split("/")[4] in self.content:
            return self._content_route(request, path)
        if path.startswith("/wp-json/contact-form-7/v1/contact-forms"):
            if not self.has_forms:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return self._forms_route(request, path)
        if path == "/wp-json/wp/v2/plugins":
            if not self.is_admin:
                return _wp_error(
                    "rest_cannot_view_plugins", "Sorry, you are not allowed to manage plugins.", 403
                )
            return _json(self._plugins())
        if path.startswith("/wp-json/rankmath/v1/ai-visibility"):
            return self._ai_visibility(request, path)

        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    # --- the schakl WordPress MCP Bridge plugin ------------------------------------------ #
    @staticmethod
    def _bridge_error(code: str, message: str, status: int, details: dict | None = None):
        """The plugin's own envelope: ``{code, message, status, details}``."""
        return httpx.Response(
            status,
            json={"code": code, "message": message, "status": status, "details": details or {}},
        )

    def _bridge_validate(self, fields: dict) -> list[dict]:
        """A sliver of the plugin's writer: choices on `kleur`, a required title on a type-10
        row. Enough to prove the *problems* shape travels; the real rules live in the plugin
        and are tested there."""
        problems: list[dict] = []
        kleur = fields.get("kleur")
        if kleur is not None and kleur not in self.bridge_choices["kleur"]:
            problems.append({
                "path": "kleur", "field": "kleur", "code": "invalid_choice",
                "message": f'"{kleur}" is not one of the choices.',
                "details": {"choices": self.bridge_choices["kleur"]},
            })
        for i, row in enumerate(fields.get("blokken_blokken") or []):
            if not isinstance(row, dict):
                continue
            if row.get("type") == "10" and not row.get("titel_0_1_2"):
                problems.append({
                    "path": f"blokken_blokken[{i}].titel_0_1_2", "field": "titel_0_1_2",
                    "code": "required", "message": '"Titel" is required in this row.',
                })
        return problems

    def _bridge_schema_groups(self) -> list[dict]:
        return [{
            "key": "group_builder", "title": "Pagina-opbouw",
            "fields": [
                {"name": "kleur", "key": "field_kleur", "type": "select", "label": "Kleur",
                 "required": False,
                 "choices": dict.fromkeys(self.bridge_choices["kleur"], ""),
                 "value_format": "one choice value (string)"},
                {"name": "blokken_blokken", "key": "field_blokken", "type": "repeater",
                 "label": "Blokken", "required": False,
                 "value_format": "array of rows, each an object of sub field names",
                 "sub_fields": [
                     {"name": "type", "key": "field_type", "type": "select",
                      "label": "Type", "required": True,
                      "choices": {"10": "Afbeelding + tekst", "15": "Werkwijze"}},
                     {"name": "titel_0_1_2", "key": "field_titel", "type": "text",
                      "label": "Titel", "required": True,
                      "conditions": [[{"field": "type", "operator": "==", "value": "10"}]]},
                 ]},
            ],
        }]

    def _bridge_record(self, row: dict, mode: str, include_schema: bool = False) -> dict:
        out = {k: v for k, v in row.items() if k not in ("fields", "references", "trid")}
        if mode != "none":
            out["fields"] = row["fields"]
            out["fields_mode"] = mode
            out["references"] = row.get("references", {})
            if include_schema:
                out["schema"] = self._bridge_schema_groups()
        if self.multilingual:
            out["translations"] = {row.get("lang", "nl"): {"id": row["id"], "this": True}}
        return out

    def _bridge_row(self, row: dict) -> dict:
        keys = ("id", "post_type", "title", "slug", "status", "link", "modified", "parent", "path")
        out = {k: row.get(k) for k in keys}
        if self.multilingual:
            out["lang"] = row.get("lang", "nl")
            out["translations"] = {row.get("lang", "nl"): row["id"]}
        return out

    def _bridge_route(self, request: httpx.Request, sub: str) -> httpx.Response:  # noqa: C901
        import json as _json_mod

        body = _json_mod.loads(request.content or b"null") if request.method != "GET" else None
        self.bridge_calls.append((request.method, sub, body))
        q = request.url.params
        parts = [p for p in sub.split("/") if p]
        body = body if isinstance(body, dict) else {}

        if sub == "/info":
            return _json({
                "plugin": {
                    "name": "schakl WordPress MCP Bridge",
                    "version": self.bridge_version,
                    "author": "schakl.",
                },
                "site": {"name": "Klant BV", "url": "https://klant.nl/", "wp_version": "7.1.2",
                         "php_version": "8.3.0", "locale": "nl_NL", "timezone": "Europe/Amsterdam"},
                "acf": {"version": "6.8.10", "pro": True},
                "wpml": {"default_language": "nl", "languages": [{"code": "nl"}, {"code": "en"}]}
                if self.multilingual else None,
                "seo": "rank_math",
                "post_types": [
                    {"slug": "page", "label": "Pagina's", "rest": True, "acf_groups": 1},
                    {"slug": "post", "label": "Berichten", "rest": True, "acf_groups": 0},
                    {"slug": "dienst", "label": "Diensten", "rest": False, "acf_groups": 1},
                ],
                "taxonomies": [{"slug": "faq_categories", "label": "Categorieën"}],
                "options_pages": [{"slug": "bedrijfsinformatie", "title": "Bedrijfsinformatie"}],
                "menus": [{"id": 1, "name": "Hoofdmenu", "slug": "hoofdmenu"}],
                "user": {"id": 1, "login": self.username, "capabilities": {"manage_options": True}},
            })

        if sub == "/schema":
            if q.get("post_type") not in (None, "page", "dienst") and not q.get("id"):
                return self._bridge_error("not_found", "That post type was not found.", 404,
                                          {"post_type": q.get("post_type")})
            return _json({
                "subject": dict(q),
                "groups": self._bridge_schema_groups(),
                "notes": {"conditions": "…"},
            })

        if sub == "/content" and request.method == "GET":
            post_type = q.get("post_type", "page")
            if post_type not in ("page", "post", "dienst"):
                return self._bridge_error(
                    "not_found", "That post type was not found.", 404,
                    {"post_type": post_type, "known": ["page", "post", "dienst"]},
                )
            statuses = set((q.get("status") or "publish,future,draft,pending,private").split(","))
            search = (q.get("search") or "").lower()
            rows = [
                r for r in self.bridge_records.values()
                if r["post_type"] == post_type
                and ("any" in statuses or r["status"] in statuses)
                and (not search or search in r["title"].lower())
                and (not q.get("lang") or q.get("lang") == "all" or r.get("lang") == q.get("lang"))
            ]
            per_page, page = int(q.get("per_page", "20")), int(q.get("page", "1"))
            chunk = rows[(page - 1) * per_page: page * per_page]
            return _json({"items": [self._bridge_row(r) for r in chunk], "total": len(rows),
                          "page": page, "per_page": per_page,
                          "pages": -(-len(rows) // per_page), "post_type": post_type})

        if sub == "/content" and request.method == "POST":
            if not body.get("title"):
                return self._bridge_error("invalid_input", "A title is required.", 400,
                                          {"field": "title"})
            problems = self._bridge_validate(body.get("fields") or {})
            if problems:
                return self._bridge_error(
                    "validation_failed", "The fields could not be saved.", 422,
                    {"problems": problems})
            wp_id = max(list(self.bridge_records) + [9000]) + 1
            row = {
                "id": wp_id, "post_type": body.get("post_type", "page"), "title": body["title"],
                "slug": body.get("slug") or f"item-{wp_id}",
                "status": body.get("status", "draft"), "link": f"https://klant.nl/?p={wp_id}",
                "modified": "2026-09-23T10:00:00+02:00", "parent": body.get("parent"),
                "path": [], "content": body.get("content", ""), "excerpt": "",
                "taxonomies": {}, "seo": body.get("seo"), "featured_media": None,
                "lang": body.get("lang", "nl"), "fields": body.get("fields") or {},
                "references": {},
            }
            self.bridge_records[wp_id] = row
            return _json(self._bridge_record(row, "compact"))

        if len(parts) >= 2 and parts[0] == "content" and parts[1].isdigit():
            wp_id = int(parts[1])
            row = self.bridge_records.get(wp_id)
            if row is None:
                return self._bridge_error("not_found", f"Record {wp_id} was not found.", 404,
                                          {"id": wp_id})
            if request.method == "GET":
                return _json(self._bridge_record(
                    row, q.get("fields", "compact"), q.get("include_schema") == "true"
                ))
            if request.method == "PATCH":
                import copy as _copy

                # Deep: a refused payload must leave the stored rows exactly as they were.
                fields = _copy.deepcopy(row["fields"])
                for name, value in (body.get("fields") or {}).items():
                    fields[name] = value
                for op in body.get("ops") or []:
                    # Just enough of the path grammar to prove ops travel and are validated.
                    kind, path = op.get("op"), str(op.get("path", ""))
                    if kind == "append":
                        fields.setdefault(path, []).append(op.get("value"))
                    elif kind == "merge" and "[" in path:
                        name, idx = path[:-1].split("[")
                        fields[name][int(idx)].update(op.get("value") or {})
                    elif kind == "set":
                        fields[path] = op.get("value")
                    else:
                        return self._bridge_error("invalid_input", f"ops: unknown op {kind}", 422,
                                                  {"op": kind})
                problems = self._bridge_validate(fields)
                if problems:
                    return self._bridge_error(
                        "validation_failed", "The fields could not be saved.", 422,
                        {"problems": problems})
                row["fields"] = fields
                for key in ("title", "status", "content", "slug", "seo"):
                    if key in body:
                        row[key] = body[key]
                self.writes.append((sub, body))
                return _json(self._bridge_record(row, body.get("mode") or "compact"))
            if request.method == "DELETE":
                force = bool(body.get("force"))
                del self.bridge_records[wp_id]
                return _json({"id": wp_id, "trashed": not force, "deleted": force})

        if sub == "/media" and request.method == "POST":
            if not body.get("url") and not body.get("base64"):
                return self._bridge_error("invalid_input", "An upload needs a url or base64.", 400)
            filename = body.get("filename") or "upload.png"
            if filename.endswith(".php"):
                return self._bridge_error("mime_not_allowed", "This site does not accept that.",
                                          422, {"filename": filename})
            wp_id = max(list(self.bridge_media) + [7800]) + 1
            att = {"id": wp_id, "url": f"https://klant.nl/wp-content/uploads/{filename}",
                   "alt": body.get("alt", ""), "title": body.get("title", ""),
                   "mime": "image/png", "width": 800, "height": 600}
            self.bridge_media[wp_id] = att
            if body.get("attach_to") and body.get("set_featured"):
                rec = self.bridge_records.get(int(body["attach_to"]))
                if rec:
                    rec["featured_media"] = wp_id
            return _json(att)

        if sub == "/terms" and request.method == "GET":
            rows = [t for t in self.bridge_terms.values() if t["taxonomy"] == q.get("taxonomy")]
            return _json({"items": rows, "total": len(rows), "taxonomy": q.get("taxonomy")})
        if sub == "/terms" and request.method == "POST":
            wp_id = max(list(self.bridge_terms) + [200]) + 1
            term = {"id": wp_id, "taxonomy": body["taxonomy"], "name": body["name"],
                    "slug": body.get("slug") or body["name"].lower(), "parent": body.get("parent"),
                    "lang": body.get("lang", "nl")}
            self.bridge_terms[wp_id] = term
            return _json(term)

        if sub == "/options" and request.method == "GET":
            return _json({"items": [{"slug": s, "title": s.title(), "post_id": "options",
                                     "capability": "edit_posts", "acf_groups": 1}
                                    for s in self.bridge_options]})
        if len(parts) == 2 and parts[0] == "options":
            page = parts[1]
            if page not in self.bridge_options:
                return self._bridge_error("not_found", "That options page was not found.", 404,
                                          {"page": page, "known": list(self.bridge_options)})
            if request.method == "PATCH":
                for name, value in (body.get("fields") or {}).items():
                    self.bridge_options[page][name] = value
                self.writes.append((sub, body))
            return _json({"page": page, "title": page.title(), "post_id": "options",
                          "fields": self.bridge_options[page], "fields_mode": "compact",
                          "references": {}})

        if sub == "/menus" and request.method == "GET":
            return _json({
                "items": [{"id": 1, "name": "Hoofdmenu", "slug": "hoofdmenu",
                           "count": len(self.bridge_menu_items), "locations": ["primary"]}],
                "locations": ["primary"],
            })
        if len(parts) >= 2 and parts[0] == "menus":
            if parts[1] not in ("1", "hoofdmenu"):
                return self._bridge_error("not_found", "That menu was not found.", 404)
            menu = {"id": 1, "name": "Hoofdmenu", "slug": "hoofdmenu"}
            if len(parts) == 3 and request.method == "POST":
                item_id = max([i["id"] for i in self.bridge_menu_items] + [500]) + 1
                self.bridge_menu_items.append({
                    "id": item_id, "title": body.get("title") or "Nieuw", "url": body.get("url"),
                    "type": "custom" if body.get("url") else "post_type", "object": "page",
                    "object_id": body.get("object_id"), "parent": body.get("parent"),
                    "order": len(self.bridge_menu_items) + 1, "children": []})
                return _json({**menu, "items": self.bridge_menu_items, "added": item_id})
            if len(parts) == 4 and request.method == "DELETE":
                item_id = int(parts[3])
                before = len(self.bridge_menu_items)
                self.bridge_menu_items = [i for i in self.bridge_menu_items if i["id"] != item_id]
                if len(self.bridge_menu_items) == before:
                    return self._bridge_error("not_found", "That menu item was not found.", 404)
                return _json({**menu, "items": self.bridge_menu_items, "removed": item_id})
            return _json({**menu, "items": self.bridge_menu_items})

        if parts and parts[0] == "forms":
            return self._bridge_forms_route(request, parts, body, q)

        if parts and parts[0] == "wpml":
            if not self.multilingual:
                return self._bridge_error(
                    "unavailable", "WPML is not available on this site: WPML is not active.", 409,
                    {"missing": "WPML"})
            if sub == "/wpml/languages":
                return _json({"default_language": "nl", "current": "nl",
                              "languages": [{"code": "nl", "default": True}, {"code": "en"}],
                              "string_translation": True})
            if sub == "/wpml/strings" and request.method == "GET":
                rows = list(self.bridge_strings.values())
                return _json({"items": rows, "total": len(rows), "page": 1, "per_page": 50})
            if sub == "/wpml/strings" and request.method == "PUT":
                row = self.bridge_strings.get(int(body.get("id") or 0))
                if not row:
                    return self._bridge_error("not_found", "That string was not found.", 404)
                row["translations"][body["lang"]] = {"value": body["value"], "complete": True}
                return _json({"id": row["id"], "lang": body["lang"], "value": body["value"],
                              "updated": True})
            if len(parts) >= 3 and parts[1] == "translations" and parts[2].isdigit():
                wp_id = int(parts[2])
                source = self.bridge_records.get(wp_id)
                if source is None:
                    return self._bridge_error("not_found", f"Record {wp_id} was not found.", 404)
                group = {r.get("lang", "nl"): {"id": r["id"], "title": r["title"],
                                               "status": r["status"], "this": r["id"] == wp_id}
                         for r in self.bridge_records.values()
                         if r.get("trid", r["id"]) == source.get("trid", wp_id)}
                if request.method == "GET":
                    return _json({"id": wp_id, "post_type": source["post_type"],
                                  "lang": source.get("lang", "nl"), "translations": group,
                                  "languages": ["nl", "en"]})
                if len(parts) == 4 and parts[3] == "connect":
                    other = self.bridge_records.get(int(body.get("translation_id") or 0))
                    if not other:
                        return self._bridge_error("invalid_input", "translation_id?", 400)
                    other["trid"] = source.get("trid", wp_id)
                    other["lang"] = body.get("lang") or other.get("lang")
                    return _json({"id": wp_id, "post_type": source["post_type"],
                                  "lang": source.get("lang", "nl"),
                                  "translations": {**group, other["lang"]: {"id": other["id"]}},
                                  "languages": ["nl", "en"]})
                lang = body.get("lang")
                if lang not in ("nl", "en"):
                    return self._bridge_error("invalid_input", f'Unknown language "{lang}".', 400,
                                              {"languages": ["nl", "en"]})
                if lang in group:
                    return self._bridge_error(
                        "translation_exists", "A translation exists already.", 409,
                        {"id": group[lang]["id"], "lang": lang})
                problems = self._bridge_validate(body.get("fields") or {})
                if problems:
                    return self._bridge_error(
                        "validation_failed", "The fields could not be saved.", 422,
                        {"problems": problems})
                new_id = max(list(self.bridge_records) + [9000]) + 1
                fields = dict(source["fields"]) if body.get("copy") != "none" else {}
                fields.update(body.get("fields") or {})
                row = {**source, "id": new_id, "title": body.get("title", source["title"]),
                       "status": body.get("status", "draft"), "lang": lang,
                       "link": f"https://klant.nl/{lang}/?p={new_id}", "fields": fields,
                       "trid": source.get("trid", wp_id)}
                self.bridge_records[new_id] = row
                out = self._bridge_record(row, "compact")
                out["source"] = {"id": wp_id, "lang": source.get("lang", "nl")}
                out["created"] = True
                return _json(out)

        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    def _bridge_form(self, row: dict) -> dict:
        out = {k: v for k, v in row.items() if k not in ("lang",)}
        out["shortcode"] = f'[contact-form-7 id="{row["hash"]}" title="{row["title"]}"]'
        out["fields"] = [
            {"name": "your-name", "type": "text", "required": True, "options": [], "values": []},
            {"name": "your-email", "type": "email", "required": True, "options": [], "values": []},
        ]
        out["messages_help"] = {"mail_sent_ok": "Sent", "validation_error": "Errors"}
        out["config_errors"] = {}
        if self.forms_translatable:
            out["lang"] = row.get("lang", "nl")
            out["translations"] = {
                r.get("lang", "nl"): r["id"] for r in self.bridge_forms.values()
                if r.get("trid", r["id"]) == row.get("trid", row["id"])
            }
        if self.multilingual:
            items = []
            if row["id"] == 6584:
                items = [dict(s) for s in self.bridge_form_strings.values()]
            packages = [{"id": 12, "kind": "Contact Form 7", "title": row["title"]}]
            out["strings"] = {"packages": packages if items else [], "items": items}
        return out

    def _bridge_forms_route(  # noqa: C901
        self, request: httpx.Request, parts: list[str], body: dict, q
    ) -> httpx.Response:
        if not self.has_forms:
            return self._bridge_error(
                "unavailable", "Contact Form 7 is not available on this site: it is not active.",
                409, {"missing": "Contact Form 7"})
        known = {"mail_sent_ok", "validation_error", "invalid_required"}
        if len(parts) == 1 and request.method == "GET":
            rows = sorted(self.bridge_forms.values(), key=lambda r: r["title"])
            if q.get("search"):
                rows = [r for r in rows if q["search"].lower() in r["title"].lower()]
            return _json({"items": [{k: v for k, v in self._bridge_form(r).items()
                                     if k in ("id", "title", "slug", "hash", "shortcode", "locale",
                                              "lang", "translations")} for r in rows],
                          "total": len(rows), "page": 1, "per_page": 50, "pages": 1})
        if len(parts) == 1 and request.method == "POST":
            if not str(body.get("title") or "").strip():
                return self._bridge_error("invalid_input", "A title is required.", 400,
                                          {"field": "title"})
            if body.get("lang") and not self.forms_translatable:
                return self._bridge_error("invalid_input", "lang applies only where …", 400,
                                          {"field": "lang"})
            wp_id = max(list(self.bridge_forms) + [6600]) + 1
            row = {"id": wp_id, "title": body["title"], "slug": body["title"].lower(),
                   "hash": f"h{wp_id}", "locale": body.get("locale") or "nl_NL",
                   "form": body.get("form") or "[text* your-name] [submit]",
                   "mail": {"active": True, "subject": "[_site_title]", "sender": "wp@klant.nl",
                            "recipient": "[_site_admin_email]", "body": "[your-name]",
                            "additional_headers": "", "attachments": "", "use_html": False,
                            "exclude_blank": False, **(body.get("mail") or {})},
                   "mail_2": {"active": False, **(body.get("mail_2") or {})},
                   "messages": {"mail_sent_ok": "Thank you.", **(body.get("messages") or {})},
                   "additional_settings": body.get("additional_settings") or "",
                   "lang": body.get("lang") or "nl"}
            self.bridge_forms[wp_id] = row
            return _json({**self._bridge_form(row), "created": True})
        wp_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        row = self.bridge_forms.get(wp_id)
        if row is None:
            return self._bridge_error("not_found", f"Form {wp_id} was not found.", 404,
                                      {"id": wp_id})
        if len(parts) == 2 and request.method == "GET":
            return _json(self._bridge_form(row))
        if len(parts) == 2 and request.method == "PATCH":
            touched = [k for k in ("title", "locale", "form", "mail", "mail_2", "messages",
                                   "additional_settings") if body.get(k) is not None]
            if not touched and not body.get("strings"):
                return self._bridge_error("invalid_input", "Nothing to update.", 400)
            if body.get("messages"):
                unknown = sorted(set(body["messages"]) - known)
                if unknown:
                    return self._bridge_error(
                        "invalid_input", f"Unknown message key(s): {unknown}.", 400,
                        {"field": "messages", "unknown": unknown, "known": sorted(known)})
            for key in ("mail", "mail_2", "messages"):
                if body.get(key) is not None:
                    row[key] = {**row[key], **body[key]}
            for key in ("title", "locale", "form", "additional_settings"):
                if body.get(key) is not None:
                    row[key] = body[key]
            translated = []
            if body.get("strings"):
                if not self.multilingual:
                    return self._bridge_error(
                        "unavailable", "WPML is not available on this site: WPML is not active.",
                        409, {"missing": "WPML"})
                for lang, values in body["strings"].items():
                    for ref, value in values.items():
                        item = next((s for s in self.bridge_form_strings.values()
                                     if s["name"] == ref or str(s["id"]) == str(ref)), None)
                        if item is None:
                            return self._bridge_error("not_found", f'String "{ref}" was not found.',
                                                      404, {"known": ["Form"]})
                        item["translations"][lang] = {"value": value, "complete": True}
                        translated.append({"id": item["id"], "lang": lang, "value": value,
                                           "updated": True, "name": item["name"]})
                touched.append("strings")
            out = {**self._bridge_form(row), "touched": touched}
            if translated:
                out["translated"] = translated
            return _json(out)
        if len(parts) == 2 and request.method == "DELETE":
            del self.bridge_forms[wp_id]
            return _json({"id": wp_id, "title": row["title"], "deleted": True})
        if len(parts) == 3 and parts[2] == "translate" and request.method == "POST":
            if not self.multilingual:
                return self._bridge_error(
                    "unavailable", "WPML is not available on this site: WPML is not active.",
                    409, {"missing": "WPML"})
            if not self.forms_translatable:
                return self._bridge_error(
                    "unavailable",
                    "Form translation is not available on this site: one form serves all.",
                    409, {"missing": "Form translation"})
            lang = body.get("lang")
            if lang not in ("nl", "en"):
                return self._bridge_error("invalid_input", f'Unknown language "{lang}".', 400,
                                          {"languages": ["nl", "en"]})
            group = {r.get("lang", "nl"): r["id"] for r in self.bridge_forms.values()
                     if r.get("trid", r["id"]) == row.get("trid", wp_id)}
            if lang in group and not body.get("overwrite"):
                return self._bridge_error("translation_exists", "A translation exists already.",
                                          409, {"id": group[lang], "lang": lang})
            new_id = max(list(self.bridge_forms) + [6600]) + 1
            copy = dict(row) if body.get("copy") != "none" else {
                "form": "", "mail": {}, "mail_2": {}, "messages": {}, "additional_settings": ""}
            made = {**copy, "id": new_id, "title": body.get("title") or row["title"],
                    "slug": f"form-{new_id}", "hash": f"h{new_id}",
                    "locale": body.get("locale") or "en_US", "lang": lang,
                    "trid": row.get("trid", wp_id)}
            for key in ("form", "additional_settings"):
                if body.get(key) is not None:
                    made[key] = body[key]
            for key in ("mail", "mail_2", "messages"):
                if body.get(key) is not None:
                    made[key] = {**made.get(key, {}), **body[key]}
            self.bridge_forms[new_id] = made
            return _json({**self._bridge_form(made), "created": True,
                          "source": {"id": wp_id, "lang": row.get("lang", "nl")}})
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    # --- surfaces ------------------------------------------------------------------------ #
    def _index(self) -> dict:
        namespaces = ["oembed/1.0", "wp/v2"]
        if self.has_abilities:
            namespaces.append("wp-abilities/v1")
        if self.rankmath_version:
            namespaces.append("rankmath/v1")
        routes: dict[str, dict] = {"/wp/v2": {"endpoints": [{"methods": ["GET"]}]}}
        if self.has_forms:
            namespaces.append("contact-form-7/v1")
        if self.multilingual:
            namespaces.append("wpml/v1")
        if self.mcp_server:
            namespaces.append("mcp")
            routes["/mcp"] = {"endpoints": [{"methods": ["GET"]}]}
            # A metadata-only route beside the transport, as breik.nl has (`mcp-oauth-server`):
            # the probe must pick the one that takes POST.
            routes["/mcp/mcp-oauth-server"] = {"endpoints": [{"methods": ["GET"]}]}
            routes[f"/mcp/{self.mcp_server}"] = {
                "endpoints": [{"methods": ["POST", "GET", "DELETE"]}]
            }
        return {
            "name": "Klant BV",
            "description": "Testen en inspecties",
            "url": "https://klant.nl",
            "namespaces": namespaces,
            "routes": routes,
            "authentication": {"application-passwords": {"endpoints": {}}},
        }

    def _me(self) -> dict:
        capabilities = {"read": True, "edit_posts": True}
        if self.is_admin:
            capabilities |= {"manage_options": True, "activate_plugins": True}
        return {
            "id": 1,
            "name": "Agency",
            "slug": self.username,
            "capabilities": capabilities,
        }

    def _abilities(self) -> list[dict]:
        rows = list(self.extra_abilities)
        if self.rankmath_version:
            rows += [
                {
                    "name": name, "label": name, "description": "", "category": "rank-math",
                    "input_schema": {}, "output_schema": {},
                    "meta": {"show_in_rest": True, "annotations": {"readonly": readonly}},
                }
                for name, readonly in (
                    ("rank-math/get-ai-visibility-overview", True),
                    ("rank-math/get-ai-visibility-brand-insights", True),
                    ("rank-math/get-ai-visibility-brand-queries", True),
                    ("rank-math/create-ai-visibility-brand", False),
                )
            ]
        return rows

    def _abilities_route(self, request: httpx.Request, path: str) -> httpx.Response:
        rows = self._abilities()
        tail = path[len("/wp-json/wp-abilities/v1/abilities") :].strip("/")
        if not tail:
            per_page = int(request.url.params.get("per_page", "10"))
            page = int(request.url.params.get("page", "1"))
            chunk = rows[(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json=chunk, headers={"X-WP-Total": str(len(rows))})
        run = tail.endswith("/run")
        name = tail[: -len("/run")] if run else tail
        spec = next((r for r in rows if r["name"] == name), None)
        if spec is None:
            return _wp_error("rest_ability_not_found", "Ability not found.", 404)
        if not run:
            return _json(spec)
        annotations = spec["meta"]["annotations"]
        expected = "GET" if annotations.get("readonly") else "POST"
        if request.method != expected:
            return _wp_error(
                "rest_ability_invalid_method", f"{expected} required.", 405
            )
        if expected == "GET":
            payload = {
                k[len("input[") : -1]: v
                for k, v in request.url.params.multi_items()
                if k.startswith("input[")
            }
        else:
            import json as _json_mod

            payload = (_json_mod.loads(request.content or b"{}") or {}).get("input")
        self.ability_runs.append((request.method, name, payload))
        if name == "core/get-environment-info":
            return _json({"wp_version": "6.9.1", "php_version": "8.3.4"})
        if name == "core/get-site-info":
            return _json({"name": "Klant BV", "language": "nl_NL", "timezone": "Europe/Amsterdam"})
        return _json({"ok": True, "input": payload})

    def _types(self) -> dict:
        return {
            "page": {"name": "Pagina's", "slug": "page", "rest_base": "pages", "hierarchical": 1},
            "post": {"name": "Berichten", "slug": "post", "rest_base": "posts"},
            "dienst": {"name": "Diensten", "slug": "dienst", "rest_base": "diensten"},
            "attachment": {"name": "Media", "slug": "attachment", "rest_base": "media"},
        }

    @staticmethod
    def _edit_shape(row: dict) -> dict:
        """What `context=edit` answers: `{raw, rendered}` pairs where a list read gives one."""
        out = dict(row)
        for key in ("title", "content", "excerpt"):
            out[key] = {"raw": row.get(key, ""), "rendered": f"<p>{row.get(key, '')}</p>"}
        return out

    def _content_route(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.split("/")
        base = parts[4]
        rows = self.content[base]
        wp_id = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None
        if request.method == "GET" and wp_id is None:
            statuses = set(request.url.params.get("status", "publish").split(","))
            lang = request.url.params.get("lang")
            search = (request.url.params.get("search") or "").lower()
            found = [
                r for r in rows.values()
                if r["status"] in statuses
                and (not lang or r.get("lang") == lang)
                and (not search or search in r["title"].lower())
            ]
            found.sort(key=lambda r: r["modified_gmt"], reverse=True)
            per_page = int(request.url.params.get("per_page", "10"))
            page = int(request.url.params.get("page", "1"))
            chunk = found[(page - 1) * per_page : page * per_page]
            return httpx.Response(
                200, json=[self._edit_shape(r) for r in chunk],
                headers={"X-WP-Total": str(len(found))},
            )
        if request.method == "GET":
            row = rows.get(wp_id)
            if row is None:
                return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            return _json(self._edit_shape(row))
        if request.method == "POST":
            import json as _json_mod

            body = _json_mod.loads(request.content or b"{}")
            self.writes.append((path, body))
            if wp_id is None:
                wp_id = max(list(rows) + [100]) + 1
                row = {
                    "id": wp_id, "type": base.rstrip("s") if base != "diensten" else "dienst",
                    "slug": body.get("slug") or f"item-{wp_id}", "status": "draft",
                    "link": f"https://klant.nl/?p={wp_id}", "title": "", "content": "",
                    "excerpt": "", "modified_gmt": "2026-09-03T10:00:00", "parent": 0,
                    "acf": {}, "meta": {}, "featured_media": 0, "lang": body.get("lang", "nl"),
                }
                rows[wp_id] = row
            else:
                row = rows.get(wp_id)
                if row is None:
                    return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            if body.get("status") not in (None, "publish", "future", "draft", "pending", "private"):
                return _wp_error("rest_invalid_param", "Invalid parameter(s): status", 400)
            for key in ("title", "content", "excerpt", "slug", "status", "template", "parent",
                        "featured_media"):
                if key in body:
                    row[key] = body[key]
            if isinstance(body.get("acf"), dict):
                row["acf"] = {**(row.get("acf") or {}), **body["acf"]}
            if isinstance(body.get("meta"), dict):
                row["meta"] = {**(row.get("meta") or {}), **body["meta"]}
            return _json(self._edit_shape(row))
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    def _media_route(self, request: httpx.Request, path: str) -> httpx.Response:
        parts = path.split("/")
        if len(parts) > 5 and parts[5].isdigit():
            row = self.media.get(int(parts[5]))
            if row is None:
                return _wp_error("rest_post_invalid_id", "Invalid post ID.", 404)
            return _json(row)
        rows = list(self.media.values())
        return httpx.Response(200, json=rows, headers={"X-WP-Total": str(len(rows))})

    def _form_read(self, row: dict) -> dict:
        """CF7's read shape: `properties.form` is `{content, fields}`; the write shape is flat."""
        fields = [
            {"type": "text*", "basetype": "text", "name": "your-name"},
            {"type": "email*", "basetype": "email", "name": "your-email"},
            {"type": "submit", "basetype": "submit", "name": ""},
        ]
        return {
            "id": row["id"], "slug": row["slug"], "title": row["title"], "locale": row["locale"],
            "properties": {
                "form": {"content": row["form"], "fields": fields},
                "mail": row["mail"], "mail_2": row["mail_2"], "messages": row["messages"],
                "additional_settings": {"content": row["additional_settings"], "settings": []},
            },
        }

    def _forms_route(self, request: httpx.Request, path: str) -> httpx.Response:
        if not self.is_admin:
            return _wp_error("wpcf7_forbidden", "You are not allowed to access contact forms.", 403)
        parts = path.split("/")
        wp_id = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None
        if request.method == "GET" and wp_id is None:
            return _json(
                [
                    {"id": r["id"], "slug": r["slug"], "title": r["title"], "locale": r["locale"]}
                    for r in self.forms.values()
                ]
            )
        if request.method == "GET":
            row = self.forms.get(wp_id)
            if row is None:
                return _wp_error("wpcf7_not_found", "Contact form not found.", 404)
            return _json(self._form_read(row))
        if request.method == "POST":
            import json as _json_mod

            body = _json_mod.loads(request.content or b"{}")
            self.writes.append((path, body))
            if wp_id is None:
                wp_id = max(list(self.forms) + [7000]) + 1
                row = {
                    "id": wp_id, "slug": f"form-{wp_id}", "title": "", "locale": "nl_NL",
                    "form": "", "mail": {}, "mail_2": {}, "messages": {}, "additional_settings": "",
                }
                self.forms[wp_id] = row
            else:
                row = self.forms.get(wp_id)
                if row is None:
                    return _wp_error(
                        "wpcf7_not_found", "The requested contact form was not found.", 404
                    )
            for key in ("title", "locale", "form", "mail", "mail_2", "messages",
                        "additional_settings"):
                if key in body and body[key] is not None:
                    row[key] = body[key]
            return _json(self._form_read(row) | {"config_errors": {}})
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    def _plugins(self) -> list[dict]:
        plugins = [
            {"plugin": "akismet/akismet", "status": "active", "name": "Akismet", "version": "5.3"}
        ]
        if self.rankmath_version:
            plugins.append(
                {
                    "plugin": RANKMATH_PLUGIN,
                    "status": "active",
                    "name": "Rank Math SEO",
                    "version": self.rankmath_version,
                }
            )
        return plugins

    def _ai_visibility(self, request: httpx.Request, path: str) -> httpx.Response:
        if not supports_ai_visibility(self.rankmath_version):
            # Absent *and* too old answer the same way, because on both the controller was never
            # registered — AI Visibility begins at 1.0.273. The fake used to serve these routes
            # for any version at all, which made it kinder than the real server on exactly the
            # state a test wants to describe: `test_an_old_rank_math…` had to switch the
            # subscription off as well, with a comment saying the routes do not exist either.
            return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
        if not self.is_admin:
            # What every AI Visibility route answers an editor: the routes exist, this user
            # may not call them. Nothing to do with the password.
            return _wp_error(
                "rest_forbidden", "Sorry, you are not allowed to do that.", 403
            )
        if not self.aiv_subscribed:
            return _wp_error(
                "aiv_unauthorized",
                "Rank Math account not connected. Please connect your account and try again.",
                401,
            )

        if path.endswith("/overview"):
            if request.url.params.get("refresh"):
                self.refresh_calls += 1
            return _json(
                {"success": True, "data": {"summary": self.summary, "brands": self.brands}}
            )

        # /brands/{id}/insights and /brands/{id}/queries — enough shape for gate 2 to build on.
        if path.endswith("/insights"):
            return _json({"success": True, "data": {"competitors": [], "query_results": []}})
        if path.endswith("/queries"):
            return _json({"success": True, "data": {"queries": [], "total": 0}})
        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
