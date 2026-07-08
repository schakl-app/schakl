/**
 * tasks web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `tasks.company` company panel, and a My Day widget.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { SquareCheckBig } from "@lucide/svelte";

import TasksPanel from "./TasksPanel.svelte";
import MyTasksWidget from "./MyTasksWidget.svelte";
import TasksByGroupWidget from "./TasksByGroupWidget.svelte";

registerWebModule({
  name: "tasks",
  nav: [
    {
      key: "tasks",
      href: "/tasks",
      label: () => t("nav.tasks"),
      module: "tasks",
      icon: SquareCheckBig,
      position: 30,
    },
  ],
  companyPanels: [
    {
      key: "tasks.company",
      module: "tasks",
      component: TasksPanel,
      position: 30,
    },
  ],
  dashboardWidgets: [
    {
      key: "tasks.my_open",
      module: "tasks",
      position: 20,
      load: (api) => api.GET("/api/v1/tasks/mine").then((r) => r.data ?? []),
      component: MyTasksWidget,
    },
    {
      key: "tasks.by_group",
      module: "tasks",
      position: 30,
      load: async (api) => {
        const [tasks, projects, companies] = await Promise.all([
          api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0 } } }),
          api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
          api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
        ]);
        return {
          tasks: tasks.data?.items ?? [],
          projects: projects.data?.items ?? [],
          companies: companies.data?.items ?? [],
        };
      },
      component: TasksByGroupWidget,
    },
  ],
});
