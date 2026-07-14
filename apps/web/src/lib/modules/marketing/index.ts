/**
 * marketing web module (epic #134): a top-level Marketing page (pick a client → GA4 / Search
 * Console / Ads sections), the company KPI panel, and — reached from those — the per-client tab
 * and the Overzicht grid. The panel's data comes from the API PanelSpec of the same key.
 * Self-registers on import via the `lib/modules` barrel.
 */
import { LineChart } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import MarketingCompanyPanel from "./MarketingCompanyPanel.svelte";

registerWebModule({
  name: "marketing",
  nav: [
    {
      key: "marketing",
      href: "/marketing",
      label: () => t("nav.marketing"),
      module: "marketing",
      icon: LineChart,
      position: 45,
      // UX-only hide; the /marketing page load and the API both re-check.
      requiresPermission: "marketing.metrics.read",
    },
  ],
  companyPanels: [
    {
      key: "marketing.overview",
      module: "marketing",
      component: MarketingCompanyPanel,
      position: 50,
    },
  ],
});
