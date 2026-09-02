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

// The entity types whose detail pages compose typed panels (projects, contacts, the
// invoicing documents — an audit trail on money is what the trail exists for, #207 —
// domains, whose pricing fields are audited since #250, and websites, which got a detail
// page of their own and therefore a place to render one).
for (const entityType of ["project", "contact", "invoice", "quote", "domain", "website"] as const) {
  registerCoreEntityPanel({
    key: "activity.trail",
    module: "activity",
    entityType,
    titleKey: "activity.title",
    position: POSITION,
    // The trail is the agency's record of its own work on the record, and the API answers a
    // portal login with an empty feed — so without this the client got the heading and *Nog
    // geen activiteit* on four of their pages. The web draws no block for what the API blanks
    // (docs/PORTAL.md); the API stays the boundary, and this is what stops the round trip.
    audience: "staff",
    // `GET /api/v1/activity` declares `activity.read`. Without this the trail rendered as an
    // empty "Geschiedenis" block for every viewer who does not hold it — and cost a 403 per
    // detail page to say so.
    requiresPermission: "activity.read",
    load: async (api, { entityId }) => {
      // One row more than the panel keeps (#407). `/api/v1/activity` answers a bare list, so
      // this is how the feed learns there is a rest without a count endpoint — the same probe
      // a task's comment list uses, and cheaper than a second query for a number the panel
      // only needs to know is non-zero.
      const { data } = await api.GET("/api/v1/activity", {
        params: {
          query: { entity_type: entityType, entity_id: entityId, limit: PANEL_LIMIT + 1 },
        },
      });
      const rows = data ?? [];
      return { items: rows.slice(0, PANEL_LIMIT), hasMore: rows.length > PANEL_LIMIT };
    },
    component: ActivityEntityPanel,
  });
}
