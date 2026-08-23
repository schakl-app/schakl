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
        #: The MCP Adapter plugin. Its namespace is per-server, which is exactly why the module
        #: discovers it rather than assuming `mcp-adapter-default-server`.
        self.mcp_namespace: str | None = "mcp/agency-server"
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

        if path in ("/wp-json", "/wp-json/"):
            return _json(self._index())
        if path == "/wp-json/wp/v2/users/me":
            return _json(self._me())
        if path == "/wp-json/wp-abilities/v1/abilities":
            if not self.has_abilities:
                return _wp_error("rest_no_route", "No route was found matching the URL.", 404)
            return _json(self._abilities())
        if path == "/wp-json/wp/v2/plugins":
            if not self.is_admin:
                return _wp_error(
                    "rest_cannot_view_plugins", "Sorry, you are not allowed to manage plugins.", 403
                )
            return _json(self._plugins())
        if path.startswith("/wp-json/rankmath/v1/ai-visibility"):
            return self._ai_visibility(request, path)

        return _wp_error("rest_no_route", "No route was found matching the URL.", 404)

    # --- surfaces ------------------------------------------------------------------------ #
    def _index(self) -> dict:
        namespaces = ["oembed/1.0", "wp/v2"]
        if self.has_abilities:
            namespaces.append("wp-abilities/v1")
        if self.rankmath_version:
            namespaces.append("rankmath/v1")
        if self.mcp_namespace:
            namespaces.append(self.mcp_namespace)
        return {
            "name": "Klant BV",
            "url": "https://klant.nl",
            "namespaces": namespaces,
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
        """Only the four this module cares about; the real list is longer and irrelevant here."""
        if not self.rankmath_version:
            return [{"name": "core/get-site-info"}]
        return [
            {"name": "core/get-site-info"},
            {"name": "rank-math/get-ai-visibility-overview"},
            {"name": "rank-math/get-ai-visibility-brand-insights"},
            {"name": "rank-math/get-ai-visibility-brand-queries"},
            {"name": "rank-math/create-ai-visibility-brand"},
        ]

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
