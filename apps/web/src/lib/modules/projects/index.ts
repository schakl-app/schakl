/**
 * projects web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item and the `projects.company` company panel.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { FolderKanban } from "@lucide/svelte";

import ProjectBudgetsDonutWidget from "./ProjectBudgetsDonutWidget.svelte";
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
      key: "projects.budgets_donut",
      module: "projects",
      position: 26,
      requiresPermission: "projects.project.read",
      descriptionKey: "dashboard.widget_desc.projects.budgets_donut",
      category: "dashboard.category.projects",
      size: "md",
      // Ten slices plus an honest tail bucket (#437): the endpoint hands back the hottest
      // rows, the tail's summed hours, and the over-budget count over the whole set.
      load: (api) =>
        api
          .GET("/api/v1/projects/dashboard-budgets", { params: { query: { limit: 10 } } })
          .then((r) => r.data ?? { items: [], total: 0 }),
      component: ProjectBudgetsDonutWidget,
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
