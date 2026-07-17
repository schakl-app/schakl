/**
 * projects web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item and the `projects.company` company panel.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { FolderKanban } from "@lucide/svelte";

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
      load: (api) =>
        api
          .GET("/api/v1/projects", {
            params: {
              query: { limit: 200, offset: 0, count: false, hours: true, status: "active" },
            },
          })
          .then((r) => r.data?.items ?? []),
      component: ProjectBudgetsWidget,
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
    },
  ],
});
