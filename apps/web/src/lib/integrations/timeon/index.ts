/**
 * timeon web module (CLAUDE.md §6, §6a) — mirrors the API integration. Business-licensed.
 *
 * It contributes a **nav item**, which is the opposite of what `snelstart`, `mollie` and
 * `cloudflare` do, and the difference is worth stating. Those three are credentials: what you
 * *work on* is an invoice or a domain, and the connection itself only ever needs configuring. A
 * two-way sync is not like that — it produces a queue. Conflicts have to be settled by a person,
 * and "a surface that has to be found is one that is not kept up to date" (the availability rule,
 * CLAUDE.md §14). So the workspace is a place you go.
 *
 * It is gated on `timeon.sync.run`, which defaults to admin only, so an employee logging hours
 * never sees it — and it disappears entirely for a tenant who has not enabled the integration,
 * because the shell renders nav from the enabled set.
 *
 * The company panel is the *server*-contributed kind: `app/integrations/timeon/panels.py`
 * declares a `PanelSpec`, the company hub asks `GET /companies/{id}/panels` for every enabled
 * module's data in one round trip, and the registry's job here is only to say which component
 * draws the key. Registering nothing would not hide it — the hub renders an unknown key as a raw
 * JSON dump.
 */
import { RefreshCw } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import TimeonCompanyPanel from "./TimeonCompanyPanel.svelte";

registerWebModule({
  name: "timeon",
  // A conversation with somebody else's service (CLAUDE.md §6a) — and what
  // `tests/unit/settings-groups.test.ts` reads to decide which Instellingen group it lands in.
  kind: "integration",
  nav: [
    {
      key: "timeon",
      href: "/timeon",
      label: () => t("nav.timeon"),
      module: "timeon",
      icon: RefreshCw,
      // Directly after Uren (70): what it answers is a question about the hours on that screen.
      position: 71,
      // UX-only hide; the page load and the API both re-check (docs/UX.md).
      requiresPermission: "timeon.sync.run",
    },
  ],
  companyPanels: [
    {
      key: "timeon.company",
      module: "timeon",
      component: TimeonCompanyPanel,
      // Beside the hours panel (40) rather than among the assets. The API says 62; this mirrors
      // it so the two orders cannot disagree.
      position: 62,
      // A client with nothing in Timeon folds into the "nog niets vastgelegd" strip (#364), and
      // the chip has somewhere to go: connecting is a settings act, never a per-client one, so
      // it points at the credential screen rather than offering a create control that would be
      // wrong on a company page.
      emptyHref: () => "/settings/timeon",
    },
  ],
});
