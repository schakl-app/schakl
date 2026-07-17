import { RefreshCcw } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import ProjectSubscriptionsPanel from "./ProjectSubscriptionsPanel.svelte";
import SubscriptionsMrrWidget from "./SubscriptionsMrrWidget.svelte";
import SubscriptionsPanel from "./SubscriptionsPanel.svelte";

registerWebModule({
  name: "subscriptions",
  dashboardWidgets: [
    {
      key: "subscriptions.mrr",
      module: "subscriptions",
      position: 30,
      requiresPermission: "subscriptions.subscription.read",
      descriptionKey: "dashboard.widget_desc.subscriptions.mrr",
      category: "dashboard.category.finance",
      size: "sm",
      load: (api) => api.GET("/api/v1/subscriptions/summary").then((r) => r.data ?? null),
      component: SubscriptionsMrrWidget,
    },
  ],
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
  entityPanels: [
    {
      // The agreements covering this project, with their included-hours burn — registered
      // rather than imported by the project page, so a tenant without `subscriptions` never
      // renders it and pays for no call.
      key: "subscriptions.project",
      module: "subscriptions",
      entityType: "project",
      titleKey: "subscriptions.panel.title",
      position: 20,
      load: async (api, { entityId }) => {
        const { data } = await api.GET("/api/v1/subscriptions", {
          params: {
            query: { entity_type: "project", entity_id: entityId, usage: true, limit: 20 },
          },
        });
        return { subscriptions: data?.items ?? [] };
      },
      component: ProjectSubscriptionsPanel,
    },
  ],
});
