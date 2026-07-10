/**
 * companies web module (CLAUDE.md §6) — mirrors the API module.
 *
 * Self-registers its nav item and the `companies.details` company panel. Importing this file
 * (via lib/modules/index.ts) performs the registration.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { Building2 } from "@lucide/svelte";

import CompanyDetailsPanel from "./CompanyDetailsPanel.svelte";

registerWebModule({
  name: "companies",
  nav: [
    {
      key: "companies",
      href: "/companies",
      label: () => t("nav.companies"),
      module: "companies",
      group: "relations",
      icon: Building2,
      position: 10,
      requiresPermission: "companies.company.read",
    },
  ],
  companyPanels: [
    {
      key: "companies.details",
      module: "companies",
      component: CompanyDetailsPanel,
      position: 10,
    },
  ],
});
