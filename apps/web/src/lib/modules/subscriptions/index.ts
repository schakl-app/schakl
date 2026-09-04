import { RefreshCcw } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import ProjectSubscriptionsPanel from "./ProjectSubscriptionsPanel.svelte";
import SubscriptionsMrrWidget from "./SubscriptionsMrrWidget.svelte";
import SubscriptionsPanel from "./SubscriptionsPanel.svelte";
import SubscriptionsPortalWidget from "./SubscriptionsPortalWidget.svelte";

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
    {
      // The client's own agreements on their homepage. `subscriptions.subscription.read:own`
      // is what the seeded `client` role holds; the API's portal repository scopes the rows.
      key: "subscriptions.portal",
      module: "subscriptions",
      audience: "portal",
      position: 20,
      requiresPermission: "subscriptions.subscription.read",
      descriptionKey: "dashboard.widget_desc.subscriptions.portal",
      category: "dashboard.category.finance",
      size: "lg",
      load: async (api, { companyId }) => {
        const { data } = await api.GET("/api/v1/subscriptions", {
          params: {
            query: { company_id: companyId ?? undefined, limit: 20, sort: "next_invoice_date" },
          },
        });
        const items = data?.items ?? [];
        return { items, total: data?.total ?? items.length, companyId };
      },
      component: SubscriptionsPortalWidget,
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
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/subscriptions?company=${id}`,
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
      requiresPermission: "subscriptions.subscription.read",
      load: async (api, { entityId }) => {
        // Five, not twenty (#407): the endpoint is paged and has always sent a total, and the
        // panel rendered all twenty and said nothing about a twenty-first.
        const { data } = await api.GET("/api/v1/subscriptions", {
          params: {
            query: { entity_type: "project", entity_id: entityId, usage: true, limit: 5 },
          },
        });
        const items = data?.items ?? [];
        // The hand-over must carry the filter (docs/UX.md), and this panel's host context does
        // not name the client — every row here belongs to the project's client, so the first
        // row does. No extra call.
        return {
          subscriptions: items,
          total: data?.total ?? items.length,
          companyId: items[0]?.company_id ?? "",
        };
      },
      component: ProjectSubscriptionsPanel,
    },
  ],
});
