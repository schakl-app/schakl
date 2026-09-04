import { Globe } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import DomainsPanel from "./DomainsPanel.svelte";
import DomainsPortalWidget from "./DomainsPortalWidget.svelte";

registerWebModule({
  name: "domains",
  dashboardWidgets: [
    {
      // The client's domain names on their homepage: name, state and renewal — never the
      // registrar, which the list hides from an external login for the same reason.
      key: "domains.portal",
      module: "domains",
      audience: "portal",
      position: 30,
      requiresPermission: "domains.domain.read",
      descriptionKey: "dashboard.widget_desc.domains.portal",
      category: "dashboard.category.assets",
      size: "lg",
      load: async (api, { companyId }) => {
        const { data } = await api.GET("/api/v1/domains", {
          params: { query: { company_id: companyId ?? undefined, limit: 50, sort: "name" } },
        });
        const items = data?.items ?? [];
        return { items, total: data?.total ?? items.length, companyId };
      },
      component: DomainsPortalWidget,
    },
  ],
  nav: [
    {
      key: "domains",
      href: "/domains",
      label: () => t("nav.domains"),
      module: "domains",
      group: "assets",
      icon: Globe,
      position: 40,
      requiresPermission: "domains.domain.read",
    },
  ],
  companyPanels: [
    {
      key: "domains.company",
      module: "domains",
      component: DomainsPanel,
      position: 40,
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/domains?company=${id}`,
    },
  ],
});
