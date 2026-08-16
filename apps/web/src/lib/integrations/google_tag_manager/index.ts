/**
 * google_tag_manager web module: the third item in the Marketing group, and the client panel
 * that links to it. Self-registers on import via the `lib/modules` barrel.
 *
 * It sits beside Marketing → Overzicht and Google Ads because the three answer different
 * questions about the same client: how they are doing, what the advertising is doing, and what
 * is measuring any of it.
 */
import { Tags } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import GtmCompanyPanel from "./GtmCompanyPanel.svelte";

registerWebModule({
  name: "google_tag_manager",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  nav: [
    {
      // A fresh, stable key: tenant nav renames and saved sidebar orderings key on it, so it
      // must never be derived from the href.
      key: "gtm",
      href: "/marketing/tag-manager",
      label: () => t("nav.gtm"),
      module: "google_tag_manager",
      group: "marketing",
      icon: Tags,
      // After Marketing → Overzicht (45) and Google Ads (46).
      position: 47,
      // UX-only hide; the page load and the API both re-check (docs/UX.md).
      requiresPermission: "google_tag_manager.container.read",
    },
  ],
  companyPanels: [
    {
      key: "google_tag_manager.company",
      module: "google_tag_manager",
      component: GtmCompanyPanel,
      // Directly under Google Ads (51), because the two read together.
      position: 52,
    },
  ],
});
