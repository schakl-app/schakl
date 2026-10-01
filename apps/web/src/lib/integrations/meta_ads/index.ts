/**
 * meta_ads web module: a client's Meta advertising, in the Marketing group. Self-registers on
 * import via the `lib/modules` barrel.
 *
 * Beside Google Ads rather than inside Social: the planner says what is being *said*, this
 * says what is being *spent*, and they are read by different people at different moments.
 *
 * **No company panel** (#411), for the reason `google_ads` gives.
 */
import Megaphone from "@lucide/svelte/icons/megaphone";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import MetaAdsCompanyPanel from "./MetaAdsCompanyPanel.svelte";

registerWebModule({
  name: "meta_ads",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  companyPanels: [
    {
      key: "meta_ads.company",
      module: "meta_ads",
      component: MetaAdsCompanyPanel,
      position: 47,
      // No ad account linked for this client: the chip goes to where one is linked.
      emptyHref: () => "/marketing/social/channels?kind=ad_account",
      emptyRequiresPermission: "meta.settings.manage",
    },
  ],
  nav: [
    {
      key: "meta_ads",
      href: "/marketing/meta-ads",
      label: () => t("nav.meta_ads"),
      module: "meta_ads",
      group: "marketing",
      icon: Megaphone,
      // After Social (48).
      position: 49,
      requiresPermission: "meta_ads.account.read",
    },
  ],
});
