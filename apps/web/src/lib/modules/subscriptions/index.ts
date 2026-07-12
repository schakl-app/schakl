import { RefreshCcw } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import SubscriptionsPanel from "./SubscriptionsPanel.svelte";

registerWebModule({
  name: "subscriptions",
  nav: [
    {
      key: "subscriptions",
      href: "/subscriptions",
      label: () => t("nav.subscriptions"),
      module: "subscriptions",
      icon: RefreshCcw,
      // With the CRM trio (#117's order): Klanten 10 · Contactpersonen 20 · Projecten 30 · here.
      position: 35,
      requiresPermission: "subscriptions.subscription.read",
    },
  ],
  companyPanels: [
    {
      key: "subscriptions.company",
      module: "subscriptions",
      component: SubscriptionsPanel,
      position: 60,
    },
  ],
});
