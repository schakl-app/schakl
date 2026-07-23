/**
 * leave web module (CLAUDE.md §6, §14) — mirrors the API module.
 * Self-registers the Verlof nav item, a My Day balance widget, and the team-absence feed
 * for the shared calendar.
 */
import { registerWebModule, type CalendarEvent, type CalendarPerson } from "$lib/core/registry";
import { isoAddDays } from "$lib/core/calendar";
import { apiErrorKey } from "$lib/core/errors";
import { fmtClockTime } from "$lib/core/format";
import { hasPermission } from "$lib/core/permissions";
import { t } from "$lib/core/i18n";
import { TreePalm } from "@lucide/svelte";

import LeaveBalanceWidget from "./LeaveBalanceWidget.svelte";
import LeavePendingWidget from "./LeavePendingWidget.svelte";
import LeaveTeamTodayWidget from "./LeaveTeamTodayWidget.svelte";
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
      position: 80,
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
    {
      key: "leave.pending_approvals",
      module: "leave",
      position: 25,
      // An approver's queue: work waiting on *you* belongs on My Day (#156).
      requiresPermission: "leave.request.approve",
      descriptionKey: "dashboard.widget_desc.leave.pending_approvals",
      category: "dashboard.category.review",
      size: "sm",
      load: async (api) => {
        const [requests, members] = await Promise.all([
          api.GET("/api/v1/leave/requests", {
            params: { query: { status: "pending", all_users: true, limit: 5 } },
          }),
          api.GET("/api/v1/members/lookup"),
        ]);
        const names = new Map((members.data ?? []).map((m) => [m.user_id, m.full_name || m.email]));
        return {
          items: (requests.data?.items ?? []).map((request) => ({
            ...request,
            user_name: names.get(request.user_id) ?? null,
          })),
          total: requests.data?.total ?? 0,
        };
      },
      component: LeavePendingWidget,
    },
    {
      key: "leave.team_today",
      module: "leave",
      position: 28,
      requiresPermission: "leave.request.read",
      descriptionKey: "dashboard.widget_desc.leave.team_today",
      category: "dashboard.category.leave",
      size: "sm",
      load: (api) => {
        const today = new Date().toISOString().slice(0, 10);
        return api
          .GET("/api/v1/leave/team", {
            params: { query: { date_from: today, date_to: today } },
          })
          .then((r) => r.data ?? []);
      },
      component: LeaveTeamTodayWidget,
    },
  ],
  calendarSources: [
    {
      key: "leave.team",
      module: "leave",
      labelKey: "leave.calendar.team",
      color: "emerald",
      load: async (
        api,
        { from, to, locale, user, color, personColors, hiddenPeople },
      ): Promise<CalendarEvent[]> => {
        const [team, types] = await Promise.all([
          api.GET("/api/v1/leave/team", {
            params: { query: { date_from: from, date_to: to } },
          }),
          api.GET("/api/v1/leave/types"),
        ]);
        const typeById = new Map(((types.data ?? []) as LeaveTypeInfo[]).map((lt) => [lt.id, lt]));
        const writeAny = hasPermission(user?.permissions, "leave.request.write", "any");
        const writeOwn = hasPermission(user?.permissions, "leave.request.write");
        // Colleagues the viewer hid from this split feed drop out entirely (#281).
        const hidden = new Set(hiddenPeople ?? []);
        return (team.data ?? [])
          .filter((item) => !hidden.has(item.user_id))
          .map((item) => {
            const leaveType = typeById.get(item.leave_type_id);
            const own = item.user_id === user?.id;
            // A chip opens *that* request (#106): your own on Mijn verlof, someone else's on the
            // approvers' Team page (whose guard redirects a non-approver back to /leave).
            const page = own ? "/leave" : "/leave/team";
            const year = item.start_date.slice(0, 4);
            // Part-day leave wears its window, time-first like any calendar (#107): "15:00–17:00
            // Stan · ADV" — otherwise someone off from 15:00 reads as away all day, and the
            // window is detail the chip (and its hover title) has nowhere else to show. An
            // omitted bound *means* the scheduled day's own start/end (#48), so the feed hands
            // the resolved window over ("until 14:00" reads 08:30–14:00) — the browser never
            // guesses a schedule. The open-ended dash survives only for a bound on an
            // unscheduled day. Times follow the personal clock preference (#13). Single-day
            // spans only: repeating "15:00 – 12:00" on every cell of a Thu-15:00 → Fri-12:00
            // chip would claim each *day* covers that window.
            const singleDay = item.start_date === item.end_date;
            const bounded = singleDay && Boolean(item.start_time || item.end_time);
            const window = bounded
              ? item.resolved_start_time && item.resolved_end_time
                ? `${fmtClockTime(item.resolved_start_time)}–${fmtClockTime(item.resolved_end_time)} `
                : item.start_time
                  ? `${fmtClockTime(item.start_time)} – `
                  : `– ${fmtClockTime(item.end_time ?? "")} `
              : "";
            // Draw this type by the hour rather than as a full-day bar (#270)? A tenant choice per
            // leave type, and the only way roostervrije tijd / ADV can be drawn per hour at all:
            // its generated days carry no times of their own, so there is nothing on the request
            // to infer a window from — the API resolves the scheduled day and hands over the two
            // instants (`starts_at`/`ends_at`), the one field `TimeGrid` positions a block by.
            //
            // Wall clock → instant is deliberately *not* done here: the org zone bridges them and
            // that conversion is the API's job, so a block still starts at 08:30 on the two days a
            // year the clocks move (`tasks/schedule.ts`, §8). A multi-day span gets no instants
            // from the API and so stays an all-day chip — one block from Monday morning to Friday
            // evening would also claim every night in between, which is the same reason the window
            // text above is single-day only.
            //
            // One consequence, deliberate: `TimeGrid` offers drag-to-reschedule on all-day chips
            // only, so a type drawn per hour is not draggable in the day/week views. The month
            // grid ignores `startsAt` entirely and keeps its drag, as does the request form.
            const asBlock =
              leaveType?.calendar_display === "timed" && Boolean(item.starts_at && item.ends_at);
            return {
              id: item.id,
              start: item.start_date,
              end: item.end_date,
              title: `${window}${item.user_name} · ${typeLabel(leaveType, locale)}`,
              // Colour precedence (#281): this colleague's own override, then the whole-feed
              // override, then the leave-type colour, then the source default. So "colour Stan
              // purple" wins over "colour Team leave blue" wins over vakantie's green.
              color: personColors?.[item.user_id] ?? color ?? leaveType?.color ?? "emerald",
              href: `${page}?year=${year}&request=${item.id}`,
              tentative: item.status === "pending",
              startsAt: asBlock ? (item.starts_at ?? undefined) : undefined,
              endsAt: asBlock ? (item.ends_at ?? undefined) : undefined,
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
      splitPeople: async (api, { user }): Promise<CalendarPerson[]> => {
        // Split the feed per colleague only for a viewer who may read others' leave; a plain
        // member sees just their own and keeps the single-colour feed (mirrors the API's team
        // gate and `tasks.scheduled.people`). Everyone the roster lists — the viewer included —
        // gets an individual colour + show/hide row (#281).
        if (!hasPermission(user?.permissions, "leave.request.read", "any")) return [];
        const { data } = await api.GET("/api/v1/members/lookup");
        return (data ?? []).map((m) => ({ id: m.user_id, name: m.full_name || m.email }));
      },
    },
    {
      // Its own source, not folded into `leave.team`: a holiday is nobody's absence, so it
      // renders as a marking rather than a chip and never counts toward a busy day (#47).
      key: "leave.holidays",
      module: "leave",
      labelKey: "leave.calendar.holidays",
      color: "slate",
      // A holiday chip is a dashed band, never a colour (#47), so there is nothing to recolour.
      colorable: false,
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
