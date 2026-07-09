/**
 * time web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `time.company` company panel, the `time.entries` panel it hangs
 * off a project's detail page, and two My Day widgets.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { Clock } from "@lucide/svelte";

import EntriesPanel from "./EntriesPanel.svelte";
import TimePanel from "./TimePanel.svelte";
import TimeTodayWidget from "./TimeTodayWidget.svelte";
import TeamMonthWidget from "./TeamMonthWidget.svelte";

/** Enough rows to answer "where did the budget go" at a glance; the rest is one click away. */
const PANEL_ENTRY_LIMIT = 8;

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
  entityPanels: [
    {
      // The hours behind a project's budget bar (#43). Registered rather than imported by the
      // project page, so a tenant with `time` disabled simply never renders it.
      key: "time.entries",
      module: "time",
      entityType: "project",
      titleKey: "time.panel.entries_title",
      position: 10,
      load: async (api, { entityId, periodStart }) => {
        // One call. `total` is the API's count over the whole period, so the panel can say it
        // truncated; summing the rows it holds could only ever count the rows it holds.
        const { data } = await api.GET("/api/v1/time/entries", {
          params: {
            query: {
              project_id: entityId,
              // The budget bar counts everyone's hours, so the rows behind it must too. Free to
              // non-managers precisely because the query names a project (see the API's `list`).
              all_users: true,
              running: false, // a running timer has logged nothing and burns no budget
              date_from: periodStart ?? undefined,
              limit: PANEL_ENTRY_LIMIT,
              offset: 0,
              sort: "-date",
            },
          },
        });
        const query = new URLSearchParams({ project_id: entityId });
        if (periodStart) query.set("date_from", periodStart);
        return {
          entries: data?.items ?? [],
          total: data?.total ?? 0,
          viewAllHref: `/overview?${query.toString()}`,
        };
      },
      component: EntriesPanel,
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
