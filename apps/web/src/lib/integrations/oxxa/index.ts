/**
 * oxxa web module (CLAUDE.md §6, issue #296) — mirrors the API module.
 *
 * It contributes no nav item, for the reason `cloudflare` contributes none: a registrar is not a
 * place you go, it is something a *domain* has. So the whole working surface is one
 * `EntityPanelSpec` on the domain detail page, and the org-wide configuration (the reseller
 * logins, the register they pull) lives under Instellingen, where docs/UX.md principle 6 puts it.
 *
 * The panel's `load` never touches OXXA — two stored-state reads — because a domain page must
 * not wait on an outside service to render and must still render when that service is down
 * (docs/PERFORMANCE.md). Going and looking is the panel's own explicit action.
 */
import { registerWebModule } from "$lib/core/registry";

import OxxaPanel from "./OxxaPanel.svelte";

registerWebModule({
  name: "oxxa",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  entityPanels: [
    {
      key: "oxxa.domain",
      module: "oxxa",
      entityType: "domain",
      titleKey: "oxxa.panel.title",
      // The register we renew at is our supplier, not the client's business (the domains list
      // hides the registrar column from an external login for the same reason) — and a panel
      // headed "Registrar (OXXA)" that then refuses is worse than none: it names the vendor
      // in the refusal.
      audience: "staff",
      // After Cloudflare's panel (30): the delegation follows the zone, and reading them in
      // that order is what makes the "push these nameservers" step legible.
      position: 40,
      load: async (api, { entityId }) => {
        // Two calls, both stored-state reads, both gated on `oxxa.registrar.sync` — a member
        // without it simply gets nulls rather than 500ing the page. The panel tells those two
        // states apart (`oxxa.panel.no_access` vs `oxxa.panel.no_account`): "you may not look"
        // and "there is nothing to look at" are different answers, and only one of them is
        // fixed by adding a credential under Instellingen.
        const [status, accounts] = await Promise.all([
          api.GET("/api/v1/oxxa/domains/{domain_id}/status", {
            params: { path: { domain_id: entityId } },
          }),
          // Needed to *name* the register a push or refresh should act through: with more than
          // one active account the API refuses to pick (`errors.oxxa_account_ambiguous`).
          api.GET("/api/v1/oxxa/accounts/options"),
        ]);
        return {
          status: status.data ?? null,
          accounts: accounts.data ?? [],
        };
      },
      component: OxxaPanel,
    },
  ],
});
