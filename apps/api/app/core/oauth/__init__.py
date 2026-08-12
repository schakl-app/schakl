"""OAuth 2.1 for MCP (docs/MCP.md, CLAUDE.md §12).

The flow issues no new kind of credential: what a client ends up holding is an ``api_keys`` row
(#20), so authorization, tenant scoping, revocation and the live-permission cap are the ones
already written. See :mod:`app.core.oauth.service` for why, and :mod:`app.core.oauth.metadata`
for the one routing fact that decides where the discovery documents are served.
"""

from app.core.oauth.router import router

__all__ = ["router"]
