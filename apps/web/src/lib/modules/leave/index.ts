/**
 * leave web module (CLAUDE.md §6, §14) — mirrors the API module.
 * Self-registers the Verlof nav item, a My Day balance widget, and the team-absence feed
 * for the shared calendar.
 */
import { registerWebModule, type CalendarEvent, type CalendarPerson } from "$lib/core/registry";
import { isoAddDays } from "$lib/core/calendar";
import { apiErrorKey } from "$lib/core/errors";
import { fmtClockTime, RANGE_DASH } from "$lib/core/format";
import { hasPermission } from "$lib/core/permissions";
import { getTimeZone } from "$lib/core/timezone";
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
        // "Today" is the org's calendar day (§8), not the server's UTC one: this load runs
        // server-side, and between 00:00 and 02:00 Amsterdam time the UTC date is yesterday.
        const today = new Intl.DateTimeFormat("en-CA", { timeZone: getTimeZone() }).format(
          new Date(),
        );
        return api
          .GET("/api/v1/leave/team", {
            // A ceiling on a read that had none (#407). One day's absences are bounded by
            // headcount, so this is a backstop rather than the tile's truncation — what the
            // tile actually does is collapse to a handful and expand in place.
            params: { query: { date_from: today, date_to: today, limit: 50 } },
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
            // Stan · Vrije tijd" — otherwise someone off from 15:00 reads as away all day, and
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
                ? `${fmtClockTime(item.resolved_start_time)}${RANGE_DASH}${fmtClockTime(item.resolved_end_time)} `
                : item.start_time
                  ? `${fmtClockTime(item.start_time)} ${RANGE_DASH} `
                  : `${RANGE_DASH} ${fmtClockTime(item.end_time ?? "")} `
              : "";
            // Draw this type by the hour rather than as a full-day bar (#270)? A tenant choice per
            // leave type, and the only way free time / vrije tijd can be drawn per hour at all:
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
            // `TimeGrid` drags positioned blocks day-granularly too, so a type drawn per hour is
            // moved from the week view like anything else — which matters most for exactly this
            // type, since a free-time day is the absence an employee is entitled to shift. The
            // window rides along and the API re-prices; the month grid ignores `startsAt` entirely
            // and keeps its own drag, as does the request form.
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
        // Active colleagues only: a departed account's historic leave still renders, but a
        // feed-menu row for someone who left is a control over nothing.
        return (data ?? [])
          .filter((m) => m.is_active)
          .map((m) => ({ id: m.user_id, name: m.full_name || m.email || "" }));
      },
    },
    {
      /**
       * Freelance availability: the days that do **not** match the week somebody was engaged
       * under. Its own source, not folded into `leave.team`, because it answers the opposite
       * question — that feed says who is away, this one says who can be booked — and a viewer
       * planning work wants to switch one off without losing the other.
       *
       * **Only the deviations are drawn.** Emitting every available day would put a chip on
       * every working day of every freelancer, which is the roster redrawn as noise; the days
       * that differ are the ones nobody already knows. That is `change`, not `deviates`: an
       * exception that moves no hours (a whole-day extra on a day already worked) is a real row
       * and not a difference, and drawing it would announce one nobody made.
       *
       * No `move`. Dragging a chip here would edit the *row* behind it, and a row is not an
       * occurrence: one Friday of "every other Friday" would silently move the whole rhythm,
       * and dragging half a swap would leave the other half where it was. Both are worse than
       * opening the surface that owns them.
       */
      key: "leave.availability",
      module: "leave",
      labelKey: "leave.calendar.availability",
      color: "sky",
      load: async (
        api,
        { from, to, user, color, personColors, hiddenPeople },
      ): Promise<CalendarEvent[]> => {
        const { data } = await api.GET("/api/v1/leave/availability/days", {
          params: { query: { date_from: from, date_to: to, all_users: true } },
        });
        const hidden = new Set(hiddenPeople ?? []);
        return (data ?? [])
          .filter((day) => day.change && !hidden.has(day.user_id))
          .map((day) => {
            const own = day.user_id === user?.id;
            const removed = day.change === "removed";
            // A removed day has no window to state, so it says so in words; the other two say
            // when the person *can* be booked, which is the question the feed exists to answer.
            //
            // **State first, name second** — the opposite order to the absence feed, and the
            // month grid is why: a cell truncates at about twenty characters, and
            // "Lotte de Vries · Bes…" is exactly as ambiguous as no chip at all on the one bit
            // that matters. Leading with it, an unhoverable cell still separates a yes from a
            // no; the name survives in the `title` attribute and in every wider view.
            const label = removed
              ? t("leave.availability.unavailable")
              : `${t("leave.availability.available")} ${fmtClockTime(day.windows[0].start)}${RANGE_DASH}${fmtClockTime(day.windows[day.windows.length - 1].end)}`;
            // A day with hours is positioned by the hour like timed leave (#270); a removed day
            // has no block to draw and stays in the pinned all-day row.
            const timed = !removed && Boolean(day.starts_at && day.ends_at);
            return {
              id: `avail-${day.user_id}-${day.date}`,
              start: day.date,
              end: day.date,
              title: `${label} · ${day.user_name}`,
              // A chip is a *day*, and a day can be bent by more than one row; the first is the
              // one to open, and both pages resolve a move's other half from it. Own → the
              // section on Mijn verlof; someone else's → the roster, whose ⋯ owns that person's
              // surface. Without an id the chip navigated to a page and left the reader hunting.
              href: `${own ? "/leave" : "/leave/team"}?availability=${day.entry_ids?.[0] ?? ""}`,
              // Colour carries the state as well as the words do, so a glance separates the two
              // without reading — and an override (#281) collapses them to one colour while the
              // text still says which is which, which is why the state was never *only* a colour.
              //
              // Both are tokens from the shared palette (`core/ui/colors`). `slate` is not in it:
              // the holidays feed names it and gets away with it only because `kind: "holiday"`
              // draws a dashed band and never reads the token, so a chip asking for it renders
              // with no fill at all — the loudest thing on the feed as the faintest thing on the
              // screen. `amber` says "not available" without `red`'s "something went wrong".
              color: personColors?.[day.user_id] ?? color ?? (removed ? "amber" : "sky"),
              startsAt: timed ? (day.starts_at ?? undefined) : undefined,
              endsAt: timed ? (day.ends_at ?? undefined) : undefined,
              sourceKey: "leave.availability",
            };
          });
      },
      splitPeople: async (api, { user }): Promise<CalendarPerson[]> => {
        // Same gate as the absence feed's split: a viewer who may only see their own gets one
        // colour and no per-person rows.
        if (!hasPermission(user?.permissions, "leave.availability.read", "any")) return [];
        const { data } = await api.GET("/api/v1/members/lookup");
        return (data ?? [])
          .filter((m) => m.is_active)
          .map((m) => ({ id: m.user_id, name: m.full_name || m.email || "" }));
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
