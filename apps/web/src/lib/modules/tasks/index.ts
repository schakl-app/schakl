/**
 * tasks web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `tasks.company` company panel, and a My Day widget.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";

import TasksPanel from "./TasksPanel.svelte";
import MyTasksWidget from "./MyTasksWidget.svelte";

registerWebModule({
  name: "tasks",
  nav: [
    {
      key: "tasks",
      href: "/tasks",
      label: () => t("nav.tasks"),
      module: "tasks",
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
  ],
});
