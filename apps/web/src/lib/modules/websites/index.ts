import { AppWindow } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import WebsitesPanel from "./WebsitesPanel.svelte";

registerWebModule({
  name: "websites",
  nav: [
    {
      key: "websites",
      href: "/websites",
      label: () => t("nav.websites"),
      module: "websites",
      group: "assets",
      icon: AppWindow,
      // Hosting's old slot (owner feedback): websites join domains in the assets group;
      // hosting itself moved to Instellingen.
      position: 41,
      requiresPermission: "websites.website.read",
    },
  ],
  companyPanels: [
    { key: "websites.company", module: "websites", component: WebsitesPanel, position: 50 },
  ],
});
