/**
 * marketing web module (epic #134): the company KPI panel. The marketing *tab* is a company
 * sub-route and the cross-client grid is an Overzicht tab, so this module contributes only the
 * `marketing.overview` company panel component (its data comes from the API PanelSpec of the same
 * key). Self-registers on import via the `lib/modules` barrel.
 */
import { registerWebModule } from "$lib/core/registry";

import MarketingCompanyPanel from "./MarketingCompanyPanel.svelte";

registerWebModule({
  name: "marketing",
  companyPanels: [
    {
      key: "marketing.overview",
      module: "marketing",
      component: MarketingCompanyPanel,
      position: 50,
    },
  ],
});
