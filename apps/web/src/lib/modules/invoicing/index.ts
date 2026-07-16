/**
 * invoicing web module (issue #207): nav, company panel. Routes live under
 * `routes/(app)/invoices` + `/quotes`; settings under `settings/invoicing`.
 */
import { FileText } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import InvoicingPanel from "./InvoicingPanel.svelte";

registerWebModule({
  name: "invoicing",
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
