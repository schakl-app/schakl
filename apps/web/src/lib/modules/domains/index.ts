import { Globe } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import DomainsPanel from "./DomainsPanel.svelte";

registerWebModule({
  name: "domains",
  nav: [
    {
      key: "domains",
      href: "/domains",
      label: () => t("nav.domains"),
      module: "domains",
      icon: Globe,
      position: 40,
      requiresPermission: "domains.domain.read",
    },
  ],
  companyPanels: [
    { key: "domains.company", module: "domains", component: DomainsPanel, position: 40 },
  ],
});
