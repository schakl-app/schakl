/**
 * invoicing web module (issue #207): nav, company panel. Routes live under
 * `routes/(app)/invoices` + `/quotes`; settings under `settings/invoicing`.
 */
import { FileClock, FileText } from "@lucide/svelte";

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
  // A submenu like Domeinen & websites (#277): documents and the uninvoiced-hours report
  // are two surfaces of one Facturatie group. The keys stay stable so tenant nav renames
  // and saved orderings survive the regrouping.
  nav: [
    {
      key: "invoicing",
      href: "/invoices",
      label: () => t("nav.invoices"),
      module: "invoicing",
      icon: FileText,
      position: 36,
      group: "invoicing",
      requiresPermission: "invoicing.invoice.read",
    },
    {
      key: "invoicing.uninvoiced",
      href: "/invoices/uninvoiced",
      label: () => t("nav.uninvoiced"),
      module: "invoicing",
      icon: FileClock,
      position: 37,
      group: "invoicing",
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
