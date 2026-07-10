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
      requiresPermission: "tasks.task.read",
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
      requiresPermission: "tasks.task.read",
      load: (api) => api.GET("/api/v1/tasks/mine").then((r) => r.data ?? []),
      component: MyTasksWidget,
    },
    {
      key: "tasks.by_group",
      module: "tasks",
      requiresPermission: "tasks.task.read",
      position: 30,
      load: async (api) => {
        // Grouping needs only ids/names — skip the per-task aggregates (`meta`) and the
        // discarded COUNT (`count`) on every list (see docs/PERFORMANCE.md).
        const [tasks, projects, companies] = await Promise.all([
          api.GET("/api/v1/tasks", {
            params: { query: { limit: 200, offset: 0, meta: false, count: false } },
          }),
          api.GET("/api/v1/projects", {
            params: { query: { limit: 200, offset: 0, count: false } },
          }),
          api.GET("/api/v1/companies", {
            params: { query: { limit: 200, offset: 0, count: false } },
          }),
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
