/**
 * cloudflare web module (CLAUDE.md §6, epic #278) — mirrors the API module.
 *
 * It contributes no nav item: Cloudflare is not a place you go, it is something a *domain*
 * has. So the whole working surface is one `EntityPanelSpec` on the domain detail page, and
 * the org-wide configuration (the API tokens, the synced zone inventory) lives under
 * Instellingen, where docs/UX.md principle 6 puts it.
 *
 * The panel's `load` is deliberately Cloudflare-free — stored rows only, three cheap API calls
 * — because a domain page must not wait on an outside service to render (docs/PERFORMANCE.md).
 * Going and looking is the panel's own explicit action.
 */
import { registerWebModule } from "$lib/core/registry";

import CloudflarePanel from "./CloudflarePanel.svelte";

registerWebModule({
  name: "cloudflare",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  entityPanels: [
    {
      key: "cloudflare.domain",
      module: "cloudflare",
      entityType: "domain",
      titleKey: "cloudflare.panel.title",
      position: 30,
      // All three calls below are `cloudflare.dns.read`; without it the panel was three 403s
      // and an empty box.
      requiresPermission: "cloudflare.dns.read",
      load: async (api, { entityId }) => {
        // Three calls, all stored-state reads. `accounts/options` is names-only on purpose:
        // choosing an account is `zone.manage`, not the credential screen's permission.
        const [status, projects, accounts] = await Promise.all([
          api.GET("/api/v1/cloudflare/domains/{domain_id}/status", {
            params: { path: { domain_id: entityId } },
          }),
          api.GET("/api/v1/cloudflare/pages/projects"),
          api.GET("/api/v1/cloudflare/accounts/options"),
        ]);
        return {
          status: status.data ?? null,
          projects: projects.data ?? [],
          accounts: accounts.data ?? [],
        };
      },
      component: CloudflarePanel,
    },
  ],
});
