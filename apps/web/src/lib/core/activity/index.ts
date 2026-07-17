/**
 * Registers the core activity panel on every auditable entity's detail page (issue #67).
 *
 * The company hub composes API panel providers, so the company's trail rides that seam (a core
 * `CompanyPanelSpec` keyed to the API's `activity.trail` panel). Projects and contacts compose
 * typed `EntityPanelSpec` loads, so they get one of those instead — both read the same core
 * `/api/v1/activity` feed. Registered as *core* panels (not a module's), because the trail is a
 * platform guarantee, not something a module opts other modules into.
 */
import { registerCoreCompanyPanel, registerCoreEntityPanel } from "$lib/core/registry";

import ActivityCompanyPanel from "./ActivityCompanyPanel.svelte";
import ActivityEntityPanel from "./ActivityEntityPanel.svelte";

/** A panel is a summary, not a log. Mirrors `core/activity/panels.py::PANEL_LIMIT`. */
const PANEL_LIMIT = 10;

// Last: history sits under the working surfaces, not above them.
const POSITION = 90;

registerCoreCompanyPanel({
  key: "activity.trail",
  module: "activity",
  component: ActivityCompanyPanel,
  position: POSITION,
});

// The entity types whose detail pages compose typed panels (projects, contacts, and the
// invoicing documents — an audit trail on money is what the trail exists for, #207).
for (const entityType of ["project", "contact", "invoice", "quote"] as const) {
  registerCoreEntityPanel({
    key: "activity.trail",
    module: "activity",
    entityType,
    titleKey: "activity.title",
    position: POSITION,
    load: async (api, { entityId }) => {
      const { data } = await api.GET("/api/v1/activity", {
        params: { query: { entity_type: entityType, entity_id: entityId, limit: PANEL_LIMIT } },
      });
      return { items: data ?? [], limit: PANEL_LIMIT };
    },
    component: ActivityEntityPanel,
  });
}
