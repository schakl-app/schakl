/**
 * leave web module (CLAUDE.md §6, §14) — mirrors the API module.
 * Self-registers the Verlof nav item, a My Day balance widget, and the team-absence feed
 * for the shared calendar.
 */
import { registerWebModule, type CalendarEvent } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { TreePalm } from "@lucide/svelte";

import LeaveBalanceWidget from "./LeaveBalanceWidget.svelte";
import { holidayName, typeLabel, type LeaveTypeInfo } from "./format";

registerWebModule({
  name: "leave",
  nav: [
    {
      key: "leave",
      href: "/leave",
      label: () => t("nav.leave"),
      module: "leave",
      icon: TreePalm,
      position: 45,
      requiresPermission: "leave.request.read",
    },
  ],
  dashboardWidgets: [
    {
      key: "leave.balance",
      module: "leave",
      position: 20,
      requiresPermission: "leave.request.read",
      descriptionKey: "dashboard.widget_desc.leave.balance",
      category: "dashboard.category.leave",
      size: "sm",
      load: (api) => api.GET("/api/v1/leave/summary").then((r) => r.data ?? null),
      component: LeaveBalanceWidget,
    },
  ],
  calendarSources: [
    {
      key: "leave.team",
      module: "leave",
      load: async (api, { from, to, locale }): Promise<CalendarEvent[]> => {
        const [team, types] = await Promise.all([
          api.GET("/api/v1/leave/team", {
            params: { query: { date_from: from, date_to: to } },
          }),
          api.GET("/api/v1/leave/types"),
        ]);
        const typeById = new Map(((types.data ?? []) as LeaveTypeInfo[]).map((lt) => [lt.id, lt]));
        return (team.data ?? []).map((item) => {
          const leaveType = typeById.get(item.leave_type_id);
          return {
            id: item.id,
            start: item.start_date,
            end: item.end_date,
            title: `${item.user_name} · ${typeLabel(leaveType, locale)}`,
            color: leaveType?.color ?? "emerald",
            href: "/leave",
            tentative: item.status === "pending",
          };
        });
      },
    },
    {
      // Its own source, not folded into `leave.team`: a holiday is nobody's absence, so it
      // renders as a marking rather than a chip and never counts toward a busy day (#47).
      key: "leave.holidays",
      module: "leave",
      load: async (api, { from, to, locale }): Promise<CalendarEvent[]> => {
        const { data } = await api.GET("/api/v1/leave/holidays", {
          params: { query: { date_from: from, date_to: to } },
        });
        return (data ?? []).map((holiday) => ({
          id: `holiday-${holiday.id}`,
          start: holiday.date,
          end: holiday.date,
          title: holidayName(holiday.name_i18n, locale),
          color: "slate",
          kind: "holiday" as const,
        }));
      },
    },
  ],
});
