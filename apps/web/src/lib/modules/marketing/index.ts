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
import MarketingPortalWidget from "./MarketingPortalWidget.svelte";
import MarketingSummaryWidget from "./MarketingSummaryWidget.svelte";

registerWebModule({
  name: "marketing",
  dashboardWidgets: [
    {
      // #254: a *staff* gallery widget teasing the existing marketing pages.
      key: "marketing.summary",
      module: "marketing",
      position: 45,
      requiresPermission: "marketing.metrics.read",
      descriptionKey: "dashboard.widget_desc.marketing.summary",
      category: "dashboard.category.marketing",
      size: "sm",
      load: (api) => api.GET("/api/v1/marketing/summary").then((r) => r.data ?? null),
      component: MarketingSummaryWidget,
    },
    {
      // #254 (owner call): the portal homepage is a per-user widget board like staff My Day —
      // a client orders/adds/removes tiles, and this tile carries their curated marketing
      // dashboard. The *content* stays the agency's (#192/#193): hidden tiles never leave the
      // API and layout editing stays behind the staff-only `marketing.link.manage`. Its data
      // is URL-driven (company/website switchers), so the portal page load injects it and
      // this `load` is deliberately a no-op.
      key: "marketing.portal",
      module: "marketing",
      audience: "portal",
      position: 10,
      requiresPermission: "marketing.metrics.read",
      descriptionKey: "dashboard.widget_desc.marketing.portal",
      category: "dashboard.category.marketing",
      size: "lg",
      load: async () => null,
      component: MarketingPortalWidget,
    },
  ],
  // A submenu like Domeinen & websites and Facturatie (#277): the cross-source dashboard and
  // Google Ads are two surfaces of one Marketing group. The key stays `marketing` so tenant nav
  // renames and saved orderings survive the regrouping, and the label moves to its own key —
  // `nav.overview` belongs to the org-wide /overview section and the two must stay independently
  // renameable. The group header borrows `items[0].icon`, so LineChart stays on this one.
  nav: [
    {
      key: "marketing",
      href: "/marketing",
      label: () => t("nav.marketing_overview"),
      module: "marketing",
      icon: LineChart,
      position: 45,
      group: "marketing",
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
      // Nothing here yet folds into the hub's one ＋ strip (#364) — and the chip **unfolds this
      // card in place** rather than linking away (#399). It used to point at
      // `/companies/<id>/marketing`, which is a dashboard: the one screen that can attach the
      // first source was hidden on exactly the clients that had none, and the chip replacing it
      // led somewhere that could only say "nothing linked yet". Drive and Google Ads already
      // work this way for the same reason — their connect flows live *in* the panel.
    },
  ],
});
