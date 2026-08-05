/**
 * reporting web module (issue #300): the report list and review screen, the client panel, the
 * per-client profile, and the client's own widget in the portal.
 *
 * Self-registers on import via the `lib/modules` barrel.
 */
import { FileBarChart } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import ReportingCompanyPanel from "./ReportingCompanyPanel.svelte";
import ReportingPortalWidget from "./ReportingPortalWidget.svelte";

registerWebModule({
  name: "reporting",
  nav: [
    {
      key: "reports",
      href: "/reports",
      label: () => t("nav.reports"),
      module: "reporting",
      icon: FileBarChart,
      position: 46,
      // UX-only hide; /reports re-checks and so does the API. Scoped at the floor on purpose:
      // an `:own` holder is a client login, and /reports is exactly the screen they should
      // reach — their own published reports, which is all the API will serve them.
      requiresPermission: "reporting.report.read",
    },
  ],
  companyPanels: [
    {
      key: "reporting.reports",
      module: "reporting",
      component: ReportingCompanyPanel,
      position: 55,
    },
  ],
  dashboardWidgets: [
    {
      // The client's own board (#254's portal audience): their latest report, its summary in
      // words, and the PDF to forward. Content is the agency's — the API sends only published
      // client-facing reports — while the tile itself is the client's to place.
      key: "reporting.latest",
      module: "reporting",
      audience: "portal",
      position: 15,
      requiresPermission: "reporting.report.read",
      descriptionKey: "dashboard.widget_desc.reporting.latest",
      category: "dashboard.category.marketing",
      size: "lg",
      load: async (api) => {
        const { data } = await api.GET("/api/v1/reporting/reports", {
          params: { query: { limit: 4, count: false } },
        });
        const items = data?.items ?? [];
        if (items.length === 0) return { latest: null, previous: [] };
        // The newest one is fetched in full for its narrative; the rest are links. A list row
        // deliberately carries no snapshot (docs/PERFORMANCE.md — a row carries what its screen
        // draws), so the summary needs the detail read.
        const { data: latest } = await api.GET("/api/v1/reporting/reports/{report_id}", {
          params: { path: { report_id: items[0].id } },
        });
        return { latest: latest ?? null, previous: items.slice(1) };
      },
      component: ReportingPortalWidget,
    },
  ],
});
