"""Public-demo-mode guardrails (issue #141).

Off by default. When ``SCHAKL_DEMO_MODE`` is on the instance is *publicly writable*, so on top of
RLS (tenant isolation) and RBAC (capability within a tenant) it adds a third layer: an enumerable
catalog of operations a visitor must never reach. See ``guard.py``.
"""

from app.core.demo.guard import DEMO_BLOCKED_RULES, demo_block_reason, demo_guard_middleware

__all__ = ["DEMO_BLOCKED_RULES", "demo_block_reason", "demo_guard_middleware"]
