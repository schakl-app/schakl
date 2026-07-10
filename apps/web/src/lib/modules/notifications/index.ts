/**
 * notifications web module (CLAUDE.md §6, issue #16) — mirrors the API module.
 *
 * It contributes **no nav item**: the bell in the header is the way in, and the sidebar is not
 * where you check whether anything happened. What it does contribute is the per-record activity
 * feed, hung off the client and project detail pages through the registry so neither page ever
 * imports it — a tenant with the module disabled simply never renders it, and pays for no call.
 *
 * The two panels differ only in plumbing: the client hub composes API panel *providers* (opaque
 * dicts), while a project page composes typed `EntityPanelSpec` loads. Both render `ActivityFeed`.
 */
import { registerWebModule } from "$lib/core/registry";

import ActivityCompanyPanel from "./ActivityCompanyPanel.svelte";
import ActivityEntityPanel from "./ActivityEntityPanel.svelte";

/** A panel is a summary, not a log. Mirrors `notifications/panels.py::PANEL_LIMIT`. */
const PANEL_LIMIT = 10;

registerWebModule({
  name: "notifications",
  companyPanels: [
    {
      key: "notifications.activity",
      module: "notifications",
      component: ActivityCompanyPanel,
      position: 90,
    },
  ],
  entityPanels: [
    {
      key: "notifications.activity",
      module: "notifications",
      entityType: "project",
      titleKey: "notifications.activity.title",
      // Last: history sits under the working surfaces, not above them.
      position: 90,
      load: async (api, { entityId }) => {
        const { data } = await api.GET("/api/v1/notifications/activity", {
          params: {
            query: { entity_type: "project", entity_id: entityId, limit: PANEL_LIMIT },
          },
        });
        return { items: data ?? [], limit: PANEL_LIMIT };
      },
      component: ActivityEntityPanel,
    },
  ],
});
