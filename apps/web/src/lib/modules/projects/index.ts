/**
 * projects web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item and the `projects.company` company panel.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { FolderKanban } from "@lucide/svelte";

import ProjectBudgetsOverviewWidget from "./ProjectBudgetsOverviewWidget.svelte";
import ProjectBudgetsWidget from "./ProjectBudgetsWidget.svelte";
import ProjectsPanel from "./ProjectsPanel.svelte";

registerWebModule({
  name: "projects",
  dashboardWidgets: [
    {
      key: "projects.budgets",
      module: "projects",
      position: 25,
      requiresPermission: "projects.project.read",
      descriptionKey: "dashboard.widget_desc.projects.budgets",
      category: "dashboard.category.projects",
      size: "md",
      // Four rows, sorted by burn on the server (#290). This asked for 200 active projects
      // with every assignee and custom field attached, then sliced four of them in the
      // browser — the tile's whole payload was 98% discarded (docs/PERFORMANCE.md).
      load: (api) =>
        api
          .GET("/api/v1/projects/dashboard-budgets", { params: { query: { limit: 4 } } })
          .then((r) => r.data ?? { items: [], total: 0 }),
      component: ProjectBudgetsWidget,
    },
    {
      // The key still says "donut" because it is what saved dashboard layouts name this tile
      // by (`dashboard_prefs`); the donut itself is gone. Renaming the key would drop the tile
      // off every board that had placed it.
      key: "projects.budgets_donut",
      module: "projects",
      position: 26,
      requiresPermission: "projects.project.read",
      titleKey: "dashboard.widget.projects.budgets_overview",
      descriptionKey: "dashboard.widget_desc.projects.budgets_overview",
      category: "dashboard.category.projects",
      size: "md",
      // Ten rows, hottest first: the endpoint hands back the rows, the count per burn band
      // over the whole set, and the hours past budget summed over the over-budget ones — so
      // every heading on the tile prints the number the list it opens will show.
      load: (api) =>
        api
          .GET("/api/v1/projects/dashboard-budgets", { params: { query: { limit: 10 } } })
          .then((r) => r.data ?? { items: [], total: 0 }),
      component: ProjectBudgetsOverviewWidget,
    },
  ],
  nav: [
    {
      key: "projects",
      href: "/projects",
      label: () => t("nav.projects"),
      module: "projects",
      icon: FolderKanban,
      position: 30,
      requiresPermission: "projects.project.read",
    },
  ],
  companyPanels: [
    {
      key: "projects.company",
      module: "projects",
      component: ProjectsPanel,
      position: 25,
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/projects?company=${id}`,
    },
  ],
});
