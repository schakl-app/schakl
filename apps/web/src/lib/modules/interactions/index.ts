/**
 * interactions web module (CLAUDE.md §6, issue #22) — contactmomenten, mirrors the API module.
 *
 * No nav item: the timeline lives on the records it belongs to. Self-registers the company
 * panel plus entity panels on project, contact and task detail pages. Host pages spread
 * `interactionActions` (./actions.server) into their form actions — that is the whole contract.
 */
import { registerWebModule, type EntityPanelSpec } from "$lib/core/registry";

import InteractionsCompanyPanel from "./InteractionsCompanyPanel.svelte";
import InteractionsEntityPanel from "./InteractionsEntityPanel.svelte";

/** Mirrors `interactions/panels.py::PANEL_LIMIT` — a panel is a summary, not a log. */
const PANEL_LIMIT = 8;

/** Between the working surfaces and the activity trail (90); drive links slot in at 55. */
const POSITION = 60;

const ENTITY_FIELDS = {
  project: "project_id",
  contact: "contact_id",
  task: "task_id",
} as const;

const entityPanels: EntityPanelSpec[] = Object.entries(ENTITY_FIELDS).map(
  ([entityType, field]) => ({
    key: `interactions.${entityType}`,
    module: "interactions",
    entityType,
    titleKey: "interactions.panel.title",
    position: POSITION,
    load: async (api, { entityId }) => {
      const { data } = await api.GET("/api/v1/interactions", {
        params: { query: { [field]: entityId, limit: PANEL_LIMIT, offset: 0 } },
      });
      return {
        items: data?.items ?? [],
        total: data?.total ?? 0,
        entityField: field,
      };
    },
    component: InteractionsEntityPanel,
  }),
);

registerWebModule({
  name: "interactions",
  companyPanels: [
    {
      key: "interactions.company",
      module: "interactions",
      component: InteractionsCompanyPanel,
      position: POSITION,
    },
  ],
  entityPanels,
});
