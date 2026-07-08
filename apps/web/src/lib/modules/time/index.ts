/**
 * time web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `time.company` company panel, and a My Day widget.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { Clock } from "@lucide/svelte";

import TimePanel from "./TimePanel.svelte";
import TimeTodayWidget from "./TimeTodayWidget.svelte";
import TeamMonthWidget from "./TeamMonthWidget.svelte";

registerWebModule({
  name: "time",
  nav: [
    {
      key: "time",
      href: "/time",
      label: () => t("nav.time"),
      module: "time",
      icon: Clock,
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
    {
      key: "time.team_month",
      module: "time",
      position: 15,
      requiresManage: true,
      load: async (api) => {
        const today = new Date().toISOString().slice(0, 10);
        const monthStart = today.slice(0, 8) + "01";
        const year = Number(today.slice(0, 4));
        const [report, revenue] = await Promise.all([
          api.GET("/api/v1/time/report", {
            params: { query: { limit: 1, offset: 0, date_from: monthStart, date_to: today } },
          }),
          api.GET("/api/v1/time/stats/revenue", { params: { query: { year } } }),
        ]);
        const totals = report.data?.totals;
        if (!totals) return null;
        const month = Number(today.slice(5, 7)) - 1;
        return {
          minutes: totals.minutes,
          billable_minutes: totals.billable_minutes,
          open_minutes: totals.open_minutes,
          revenue_month: revenue.data?.months_current?.[month] ?? 0,
        };
      },
      component: TeamMonthWidget,
    },
  ],
});
