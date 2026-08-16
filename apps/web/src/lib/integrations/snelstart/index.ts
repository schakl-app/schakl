/**
 * snelstart web module (CLAUDE.md §6, epic #377 / issue #31) — mirrors the API integration.
 *
 * It contributes **no nav item**, for mollie's reason: an accounting credential is not a place you
 * go. The surface you *work* on is the invoice, the client and the product list, all of which are
 * `invoicing`'s and `companies`' screens; what is left over is org-wide configuration — which
 * administration, which revenue account, does a push happen by itself — and docs/UX.md principle 6
 * puts that under Instellingen, never as a button inside a working screen.
 *
 * It does contribute a **company panel**, and the shape is worth stating because it is the other
 * one of the two (`cloudflare` uses the first). An `entityPanel` is a web-owned panel with its own
 * `load`; this one is the *server*-contributed kind — `app/integrations/snelstart/panels.py`
 * declares a `PanelSpec`, the company hub asks `GET /companies/{id}/panels` for every enabled
 * module's data in one round trip, and the registry's job here is only to say which component
 * draws the key. Registering nothing would not have hidden the panel: the hub renders an unknown
 * key as a raw JSON dump, which is exactly what a client's bookkeeping status must not look like.
 *
 * No `requiresPermission` on the entry: the *API* panel declares `snelstart.sync.run` and simply
 * does not return the panel to anybody else, so there is nothing here to gate and no second copy
 * of that rule to drift.
 */
import { registerWebModule } from "$lib/core/registry";

import SnelstartCompanyPanel from "./SnelstartCompanyPanel.svelte";

// A conversation with somebody else's service (CLAUDE.md §6a) — and what
// `tests/unit/settings-groups.test.ts` reads to decide which Instellingen group the screen lands in.
registerWebModule({
  name: "snelstart",
  kind: "integration",
  companyPanels: [
    {
      key: "snelstart.company",
      module: "snelstart",
      component: SnelstartCompanyPanel,
      // Beside the invoicing panel rather than among the assets: what it answers is a
      // bookkeeping question about this client's invoices. The API says 95; this mirrors it so
      // the two orders cannot disagree.
      position: 95,
      // A client with nothing in SnelStart yet is folded into the "nog niets vastgelegd" strip
      // (#364), and its chip has somewhere to go: connecting an administration is a settings act,
      // never a per-client one, so the chip points at the credential screen rather than offering
      // a create control that would be wrong on a company page.
      emptyHref: () => "/settings/snelstart",
    },
  ],
});
