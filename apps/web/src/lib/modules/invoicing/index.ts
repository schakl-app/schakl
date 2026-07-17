/**
 * invoicing web module (issue #207): nav, company panel. Routes live under
 * `routes/(app)/invoices` + `/quotes`; settings under `settings/invoicing`.
 */
import { FileText } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import InvoicingOutstandingWidget from "./InvoicingOutstandingWidget.svelte";
import InvoicingPanel from "./InvoicingPanel.svelte";
import QuotesOpenWidget from "./QuotesOpenWidget.svelte";

registerWebModule({
  name: "invoicing",
  dashboardWidgets: [
    {
      key: "invoicing.outstanding",
      module: "invoicing",
      position: 35,
      requiresPermission: "invoicing.invoice.read",
      descriptionKey: "dashboard.widget_desc.invoicing.outstanding",
      category: "dashboard.category.finance",
      size: "sm",
      load: (api) => api.GET("/api/v1/invoicing/summary").then((r) => r.data ?? null),
      component: InvoicingOutstandingWidget,
    },
    {
      key: "invoicing.quotes_open",
      module: "invoicing",
      position: 40,
      requiresPermission: "invoicing.quote.read",
      descriptionKey: "dashboard.widget_desc.invoicing.quotes_open",
      category: "dashboard.category.finance",
      size: "sm",
      load: (api) => api.GET("/api/v1/invoicing/summary").then((r) => r.data ?? null),
      component: QuotesOpenWidget,
    },
  ],
  nav: [
    {
      key: "invoicing",
      href: "/invoices",
      label: () => t("nav.invoicing"),
      module: "invoicing",
      icon: FileText,
      position: 36,
      requiresPermission: "invoicing.invoice.read",
    },
  ],
  companyPanels: [
    {
      key: "invoicing.company",
      module: "invoicing",
      component: InvoicingPanel,
      position: 65,
    },
  ],
});
