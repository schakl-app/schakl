/**
 * wordpress web module (CLAUDE.md §6, docs/WORDPRESS.md) — mirrors the API module.
 *
 * It contributes no nav item, for `cloudflare`'s and `uptime`'s reason: WordPress is not a place
 * you go, it is something a *website* has. So the whole working surface is one `EntityPanelSpec`
 * on the website detail page — which needs no edit to receive it, because that page already
 * renders `entityPanelsFor(enabled, "website", user)`.
 *
 * The panel's `load` is deliberately WordPress-free: one stored-state read, no outside service,
 * so a website page renders at the same speed whether the client's site is up, down, behind a
 * firewall or gone (docs/PERFORMANCE.md). Going and looking is the panel's own explicit action.
 *
 * Position 20 puts it above `uptime` (40): the credential is what the other integrations on this
 * page hang off, so it reads first.
 */
import { registerWebModule } from "$lib/core/registry";

import WordPressPanel from "./WordPressPanel.svelte";

registerWebModule({
  name: "wordpress",
  entityPanels: [
    {
      key: "wordpress.website",
      module: "wordpress",
      entityType: "website",
      titleKey: "wordpress.panel.title",
      position: 20,
      // The key the call actually makes, not the one the screen is about (#310). The panel
      // reads `wordpress.site.read`; its *controls* self-gate on `wordpress.site.manage`,
      // because a member may see that a site is connected without being able to rotate the
      // administrator password behind it.
      requiresPermission: "wordpress.site.read",
      load: async (api, { entityId }) => {
        const site = await api.GET("/api/v1/wordpress/sites/by-website/{website_id}", {
          params: { path: { website_id: entityId } },
        });
        // `null` is the ordinary state of most websites, not an error: the endpoint answers it
        // rather than 404ing so this draws an empty state without logging one per page view.
        return { site: site.data ?? null };
      },
      component: WordPressPanel,
    },
  ],
});
