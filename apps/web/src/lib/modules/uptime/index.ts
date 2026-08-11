/**
 * uptime web module (CLAUDE.md §6, docs/UPTIME.md) — mirrors the API module.
 *
 * It contributes no nav item, for `cloudflare`'s reason: uptime is not a place you go, it is
 * something a *website* has. So the working surface is one `EntityPanelSpec` on the website
 * detail page, and the org-wide configuration — the instances and their credentials — lives
 * under Instellingen, where docs/UX.md principle 6 puts it.
 *
 * The panel's `load` is deliberately Kuma-free: one stored-state read, no outside service, so
 * a website page renders at the same speed whether the client's monitoring is up, down or
 * unreachable (docs/PERFORMANCE.md). Going and looking is the settings screen's explicit action.
 */
import { registerWebModule } from "$lib/core/registry";

import UptimePanel from "./UptimePanel.svelte";

registerWebModule({
  name: "uptime",
  entityPanels: [
    {
      key: "uptime.website",
      module: "uptime",
      entityType: "website",
      titleKey: "uptime.panel.title",
      position: 40,
      // The key the call actually makes, not the one the screen is about (#310). Without it the
      // panel is a 403 and an empty box.
      requiresPermission: "uptime.monitor.read",
      load: async (api, { entityId }) => {
        const monitors = await api.GET("/api/v1/uptime/monitors", {
          // `count=false`: the panel draws rows, not a total, and a count it never renders is
          // a second query per page load (docs/PERFORMANCE.md).
          params: { query: { website_id: entityId, limit: 50, offset: 0, count: false } },
        });
        return { monitors: monitors.data?.items ?? [] };
      },
      component: UptimePanel,
    },
  ],
});
