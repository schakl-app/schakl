"""AI core (epic #131, issue #126) — a cross-cutting capability like custom fields (§13),
RBAC (§15) and the activity trail (§16), not a module.

Owns the tenant's provider configuration (encrypted key), the one ``AIService`` every AI
feature calls, per-feature toggles, usage metering, and the executable tool registry the
contextual assistant runs on. Licensed as its own sku (``ai``, issue #137): whether it is
sold together with automation is a license-document decision, never code coupling.
"""

from __future__ import annotations

from app.core.ai.models import AI_FEATURES, AI_PROVIDERS, AIReport, AISettings, AIUsage
from app.core.ai.service import AIService, AISettingsService, enabled_features
from app.core.ai.tools import AIToolSpec, Source, ToolResult

__all__ = [
    "AI_FEATURES",
    "AI_PROVIDERS",
    "AIReport",
    "AIService",
    "AISettings",
    "AISettingsService",
    "AIToolSpec",
    "AIUsage",
    "Source",
    "ToolResult",
    "enabled_features",
]
