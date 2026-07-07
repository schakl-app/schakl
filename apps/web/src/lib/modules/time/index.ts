/**
 * time web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `time.company` company panel, and a My Day widget.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";

import TimePanel from "./TimePanel.svelte";
import TimeTodayWidget from "./TimeTodayWidget.svelte";

registerWebModule({
  name: "time",
  nav: [
    {
      key: "time",
      href: "/time",
      label: () => t("nav.time"),
      module: "time",
      position: 40,
    },
  ],
  companyPanels: [
    {
      key: "time.company",
      module: "time",
      component: TimePanel,
      position: 40,
    },
  ],
  dashboardWidgets: [
    {
      key: "time.today",
      module: "time",
      position: 10,
      load: (api) => api.GET("/api/v1/time/summary").then((r) => r.data ?? null),
      component: TimeTodayWidget,
    },
  ],
});
