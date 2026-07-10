"""notifications module (CLAUDE.md §6, issue #16) — in-app inbox, activity feed, delivery prefs.

Importing this package self-registers the module into the shared registry. It owns no company
panel of its own domain; instead it *subscribes* to the events every other module emits
(task/project/company/leave/time) and fans each out to the people who care, honouring their
per-event delivery preferences. Read-first, tenant-scoped, i18n in the recipient's locale.
"""

from __future__ import annotations

from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="notifications",
    i18n_namespace="notifications",
)

registry.register(module)
