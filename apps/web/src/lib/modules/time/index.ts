/**
 * time web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `time.company` company panel, the `time.entries` panel it hangs
 * off a project's detail page, and two My Day widgets.
 */
import { registerWebModule } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { orgToday } from "$lib/core/today";
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
      position: 70,
      requiresPermission: "time.entry.read",
    },
  ],
  companyPanels: [
    {
      key: "time.company",
      module: "time",
      component: TimePanel,
      position: 40,
      // "Alle uren →" rides the heading line (#400): the host's <h2> would otherwise have left
      // the link floating in a band of its own under it.
      ownsHeader: true,
      // Nothing here yet folds into the hub's one ＋ strip (#364) — as a chip that **unfolds the
      // card in place**, not as a link to `/time?company=…`. The panel's own ＋ is a dialog now
      // (#402), so sending someone to the timesheet to log their first hour would have been the
      // one-way trip this panel just stopped making, kept alive on the empty client.
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
      requiresPermission: "time.entry.read",
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
      requiresPermission: "time.entry.read",
      descriptionKey: "dashboard.widget_desc.time.today",
      category: "dashboard.category.time",
      size: "sm",
      load: (api) => api.GET("/api/v1/time/summary").then((r) => r.data ?? null),
      component: TimeTodayWidget,
    },
    {
      key: "time.team_month",
      module: "time",
      position: 15,
      requiresPermission: "time.report.read",
      descriptionKey: "dashboard.widget_desc.time.team_month",
      category: "dashboard.category.time",
      size: "md",
      load: async (api) => {
        const today = orgToday();
        const monthStart = today.slice(0, 8) + "01";
        const { data } = await api.GET("/api/v1/time/stats/team-summary", {
          params: { query: { date_from: monthStart, date_to: today } },
        });
        if (!data) return null;
        return {
          minutes: data.minutes,
          billable_minutes: data.billable_minutes,
          open_minutes: data.open_minutes,
          revenue_month: data.revenue,
        };
      },
      component: TeamMonthWidget,
    },
  ],
});
