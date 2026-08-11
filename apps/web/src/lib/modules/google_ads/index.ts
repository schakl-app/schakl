/**
 * google_ads web module: the second item in the Marketing group, and the client panel that
 * links to it. Self-registers on import via the `lib/modules` barrel.
 *
 * It sits beside Marketing → Overzicht rather than inside it because the two answer different
 * questions: the dashboard says how a client is doing across every channel, this says what the
 * advertising is doing and what to change about it.
 */
import { Megaphone } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import GoogleAdsCompanyPanel from "./GoogleAdsCompanyPanel.svelte";

registerWebModule({
  name: "google_ads",
  nav: [
    {
      // A fresh, stable key: tenant nav renames and saved sidebar orderings key on it, so it
      // must never be derived from the href.
      key: "google_ads",
      href: "/marketing/google-ads",
      label: () => t("nav.google_ads"),
      module: "google_ads",
      group: "marketing",
      icon: Megaphone,
      // After Marketing → Overzicht (45). The group header borrows `items[0].icon`, so the
      // order also decides which icon labels the group — LineChart stays on Overzicht.
      position: 46,
      // UX-only hide; the page load and the API both re-check (docs/UX.md).
      requiresPermission: "google_ads.account.read",
    },
  ],
  companyPanels: [
    {
      key: "google_ads.company",
      module: "google_ads",
      component: GoogleAdsCompanyPanel,
      // Directly under the marketing panel (50), because the two read together.
      position: 51,
    },
  ],
});
