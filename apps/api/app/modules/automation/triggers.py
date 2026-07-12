"""Trigger catalog (issue #27) — which bus events a rule may fire on.

The single source of truth for the automation module's event vocabulary: the module
``__init__`` subscribes exactly these, the rule editor's trigger select renders exactly these,
and rule validation accepts nothing else. Each trigger names the entity the event is about
(where its id rides in the payload) and, when the owning module has shipped, the table a
condition snapshot is read from.

``website.uptime_toggled`` and ``domain.status_changed`` are the #96 recipes: declared here so
a tenant can configure the rules today, they start flowing the moment the websites/domains
modules (#94, #87) land and ``emit`` them. Until then their ``table`` is ``None`` — conditions
evaluate against the event payload only.

Snapshot reads are by table name only (the sanctioned cross-module read, see
``tasks/service.py``'s ``time_entries`` sum) and the table name can only ever come from this
dict — never from user input.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerSpec:
    event: str
    entity_type: str          # what the run row records
    id_key: str               # payload key carrying the subject's id
    table: str | None = None  # snapshot source; None = module not shipped yet / no table

    @property
    def i18n_key(self) -> str:
        return f"automation.trigger.{self.event}"


TRIGGERS: dict[str, TriggerSpec] = {
    spec.event: spec
    for spec in (
        TriggerSpec("task.created", "task", "task_id", "tasks"),
        TriggerSpec("task.status_changed", "task", "task_id", "tasks"),
        TriggerSpec("task.assigned", "task", "task_id", "tasks"),
        TriggerSpec("company.created", "company", "company_id", "companies"),
        TriggerSpec("company.status_changed", "company", "company_id", "companies"),
        TriggerSpec("project.status_changed", "project", "project_id", "projects"),
        # #96 recipes — forward-declared; the owning modules are issues #94 / #87.
        TriggerSpec("website.uptime_toggled", "website", "website_id", None),
        TriggerSpec("domain.status_changed", "domain", "domain_id", None),
    )
}
