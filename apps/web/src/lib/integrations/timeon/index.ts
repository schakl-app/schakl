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
 * **No company panel** (#411), and the loss is deliberate rather than overlooked: the hub's card
 * carried this client's pairing count and their open conflicts, and nothing takes its place.
 * Timeon is a cutover integration whose home is `/timeon` — the screen somebody opens *because* a
 * sync is running — and a card on every client's page for a migration that ends is a card that
 * outlives its reason. The conflicts queue is where a decision is actually made; the hub only
 * ever said that one was waiting.
 */
import { RefreshCw } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

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
});
