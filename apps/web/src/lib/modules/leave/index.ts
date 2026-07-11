/**
 * leave web module (CLAUDE.md §6, §14) — mirrors the API module.
 * Self-registers the Verlof nav item, a My Day balance widget, and the team-absence feed
 * for the shared calendar.
 */
import { registerWebModule, type CalendarEvent } from "$lib/core/registry";
import { isoAddDays } from "$lib/core/calendar";
import { apiErrorKey } from "$lib/core/errors";
import { hasPermission } from "$lib/core/permissions";
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
      load: async (api, { from, to, locale, user }): Promise<CalendarEvent[]> => {
        const [team, types] = await Promise.all([
          api.GET("/api/v1/leave/team", {
            params: { query: { date_from: from, date_to: to } },
          }),
          api.GET("/api/v1/leave/types"),
        ]);
        const typeById = new Map(((types.data ?? []) as LeaveTypeInfo[]).map((lt) => [lt.id, lt]));
        const writeAny = hasPermission(user?.permissions, "leave.request.write", "any");
        const writeOwn = hasPermission(user?.permissions, "leave.request.write");
        return (team.data ?? []).map((item) => {
          const leaveType = typeById.get(item.leave_type_id);
          const own = item.user_id === user?.id;
          // A chip opens *that* request (#106): your own on Mijn verlof, someone else's on the
          // approvers' Team page (whose guard redirects a non-approver back to /leave).
          const page = own ? "/leave" : "/leave/team";
          const year = item.start_date.slice(0, 4);
          return {
            id: item.id,
            start: item.start_date,
            end: item.end_date,
            title: `${item.user_name} · ${typeLabel(leaveType, locale)}`,
            color: leaveType?.color ?? "emerald",
            href: `${page}?year=${year}&request=${item.id}`,
            tentative: item.status === "pending",
            sourceKey: "leave.team",
            // Offer the drag only where an edit could succeed; the API stays the boundary
            // (hours recompute, re-approval per #72, the past lock, self-approval per #110).
            draggable: own ? writeOwn : writeAny,
          };
        });
      },
      move: async (api, { id, deltaDays }) => {
        // The move is an edit: shift the whole span, keep the times, and let the server
        // recompute hours and re-trigger approval — the browser is never the authority (§14).
        const current = await api.GET("/api/v1/leave/requests/{request_id}", {
          params: { path: { request_id: id } },
        });
        if (current.error) return apiErrorKey(current.error).key;
        const request = current.data;
        const { error } = await api.PATCH("/api/v1/leave/requests/{request_id}", {
          params: { path: { request_id: id } },
          body: {
            start_date: isoAddDays(request.start_date, deltaDays),
            end_date: isoAddDays(request.end_date, deltaDays),
          },
        });
        return error ? apiErrorKey(error).key : null;
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
