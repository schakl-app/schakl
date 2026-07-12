import { Server } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import HostingPanel from "./HostingPanel.svelte";

registerWebModule({
  name: "hosting",
  nav: [
    {
      key: "hosting",
      href: "/hosting",
      label: () => t("nav.hosting"),
      module: "hosting",
      group: "assets",
      icon: Server,
      position: 41,
      requiresPermission: "hosting.hosting.read",
    },
  ],
  companyPanels: [
    { key: "hosting.company", module: "hosting", component: HostingPanel, position: 50 },
  ],
});
