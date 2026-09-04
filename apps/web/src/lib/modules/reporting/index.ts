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
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/companies/${id}/reporting`,
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
      // Second on the client's board (#451): what was asked of them, then what was written
      // for them, then the live numbers.
      position: 10,
      requiresPermission: "reporting.report.read",
      descriptionKey: "dashboard.widget_desc.reporting.latest",
      category: "dashboard.category.marketing",
      size: "lg",
      load: async (api, { companyId }) => {
        // `count: true` (#407): the tile listed four earlier reports and had no way to say a
        // fifth existed, because no total was ever computed. On the selected company: the
        // board is one company at a time. The list row carries everything the cover draws
        // (period, publication, the PDF's id) — the detail read that fetched the narrative is
        // gone with the narrative.
        const { data } = await api.GET("/api/v1/reporting/reports", {
          params: { query: { limit: 4, count: true, company_id: companyId ?? undefined } },
        });
        const items = data?.items ?? [];
        const total = data?.total ?? items.length;
        if (items.length === 0) return { latest: null, previous: [], total };
        return { latest: items[0], previous: items.slice(1), total };
      },
      component: ReportingPortalWidget,
    },
  ],
});
