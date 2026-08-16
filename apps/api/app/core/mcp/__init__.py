"""MCP server (CLAUDE.md §12) — the API surface as tools, guarded by API keys.

This package is the only importer of ``fastmcp`` in the codebase, which is why the one piece
of noise that import makes is silenced here, at its door, rather than anywhere with a wider
blast radius.
"""

import warnings

# ``import fastmcp`` reaches its JWT verifier, which imports ``authlib.jose`` — deprecated in
# Authlib 1.7 in favour of ``joserfc``. None of it is ours to fix: MCP authenticates with API
# keys (§12) so that provider is never mounted, we never touch ``authlib.jose`` ourselves, and
# there is no version to move to — the import is still there on FastMCP 3.x, and the pin
# (``fastmcp>=2.12,<3``) is deliberate.
#
# What makes it *loud* rather than merely present is that ``authlib/deprecate.py`` runs
# ``simplefilter("always", AuthlibDeprecationWarning)`` at import time. An ordinary
# DeprecationWarning is invisible outside ``__main__``; this one prints on every API boot,
# every worker boot and every test run.
#
# So it is silenced for the length of one import and no longer. A blanket
# ``ignore::DeprecationWarning`` would also hide the deprecations we *can* act on: we are an
# Authlib consumer in our own right (OIDC, Google OAuth), and Authlib 2.0 is where those land.
# And the filter names the module that raises it, so if FastMCP ever moves or drops the import
# the warning comes back rather than staying silenced against a line that no longer exists.
#
# ``authlib.deprecate`` is imported first, and that is load-bearing twice over.
# ``simplefilter`` *prepends*: were Authlib's ``always`` installed inside the block — which is
# what happens when FastMCP is the first thing to reach it — it would sit in front of the
# ignore below and the warning would print anyway. And ``catch_warnings`` restores the filter
# list it entered with, so an ``always`` installed inside the block is also taken back out on
# the way through, costing us every Authlib deprecation thereafter.
import authlib.deprecate  # noqa: F401 — for its import-time filter, which must predate ours

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="authlib.jose module is deprecated",
        category=DeprecationWarning,
        module=r"fastmcp\.server\.auth\.providers\.jwt",
    )
    from app.core.mcp.server import build_mcp_asgi_app  # noqa: E402

__all__ = ["build_mcp_asgi_app"]
