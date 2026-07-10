/**
 * leave web module (CLAUDE.md §6, §14) — mirrors the API module.
 * Self-registers the Verlof nav item, a My Day balance widget, and the team-absence feed
 * for the shared calendar.
 */
import { registerWebModule, type CalendarEvent } from "$lib/core/registry";
import { t } from "$lib/core/i18n";
import { TreePalm } from "@lucide/svelte";

import LeaveBalanceWidget from "./LeaveBalanceWidget.svelte";
import { typeLabel, type LeaveTypeInfo } from "./format";

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
  ],
});
