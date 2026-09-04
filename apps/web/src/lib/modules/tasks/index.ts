/**
 * tasks web module (CLAUDE.md §6, §10) — mirrors the API module.
 * Self-registers a nav item, the `tasks.company` company panel, a My Day widget, and the two
 * calendar feeds: planned task blocks (#188) and task deadlines.
 */
import { isoAddDays } from "$lib/core/calendar";
import { apiErrorKey } from "$lib/core/errors";
import { t } from "$lib/core/i18n";
import { hasPermission } from "$lib/core/permissions";
import { registerWebModule, type CalendarEvent, type CalendarPerson } from "$lib/core/registry";
import { orgToday } from "$lib/core/today";
import { SquareCheckBig } from "@lucide/svelte";

import { localDayTime } from "./schedule";
import TasksPanel from "./TasksPanel.svelte";
import MyTasksWidget from "./MyTasksWidget.svelte";
import PortalTasksWidget from "./PortalTasksWidget.svelte";
import PortalWorkWidget from "./PortalWorkWidget.svelte";
import TasksByGroupWidget from "./TasksByGroupWidget.svelte";

registerWebModule({
  name: "tasks",
  nav: [
    {
      key: "tasks",
      href: "/tasks",
      label: () => t("nav.tasks"),
      module: "tasks",
      icon: SquareCheckBig,
      position: 60,
      requiresPermission: "tasks.task.read",
    },
  ],
  companyPanels: [
    {
      key: "tasks.company",
      module: "tasks",
      component: TasksPanel,
      position: 30,
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/tasks?company_id=${id}`,
    },
  ],
  dashboardWidgets: [
    {
      // The client's homepage tile (#450): first on the board, because what is asked of the
      // client is the one thing on it they are expected to act on — the open tasks assigned
      // to one of the client's own people, on the company the switcher selected.
      key: "tasks.portal",
      module: "tasks",
      audience: "portal",
      position: 5,
      requiresPermission: "tasks.task.read",
      descriptionKey: "dashboard.widget_desc.tasks.portal",
      category: "dashboard.category.tasks",
      size: "lg",
      load: async (api, { companyId }) => {
        const { data } = await api.GET("/api/v1/tasks", {
          params: {
            query: {
              open: true,
              assigned_to: "contact",
              company_id: companyId ?? undefined,
              limit: 20,
              sort: "due_date",
              // The contact's name — who at the client this is asked of — is resolved with
              // the row aggregates, and it is the one thing this tile prints beside the title.
              meta: true,
              count: true,
            },
          },
        });
        const items = data?.items ?? [];
        return { items, total: data?.total ?? items.length, companyId };
      },
      component: PortalTasksWidget,
    },
    {
      // "Werkzaamheden": what the agency is doing for the client — the account's other open
      // work, by urgency. The second tile, right under what was asked of them (#451's order).
      key: "tasks.portal_work",
      module: "tasks",
      audience: "portal",
      position: 6,
      requiresPermission: "tasks.task.read",
      descriptionKey: "dashboard.widget_desc.tasks.portal_work",
      category: "dashboard.category.tasks",
      size: "lg",
      load: async (api, { companyId }) => {
        const { data } = await api.GET("/api/v1/tasks", {
          params: {
            query: {
              open: true,
              assigned_to: "agency",
              company_id: companyId ?? undefined,
              limit: 100,
              sort: "due",
              meta: false,
              count: true,
            },
          },
        });
        const items = data?.items ?? [];
        return { items, total: data?.total ?? items.length, companyId };
      },
      component: PortalWorkWidget,
    },
    {
      key: "tasks.my_open",
      module: "tasks",
      position: 20,
      requiresPermission: "tasks.task.read",
      descriptionKey: "dashboard.widget_desc.tasks.my_open",
      category: "dashboard.category.tasks",
      size: "md",
      load: (api) =>
        api
          .GET("/api/v1/tasks/dashboard-mine")
          // The bucket counts come from the API now (#407): derived in the browser off a
          // page of twenty they were wrong numbers for anyone with more open work. Four of
          // them since #397 — "upcoming" was the week and the rest as one number.
          .then(
            (r) =>
              r.data ?? { items: [], total: 0, overdue: 0, due_today: 0, due_week: 0, later: 0 },
          ),
      component: MyTasksWidget,
    },
    {
      key: "tasks.by_group",
      module: "tasks",
      requiresPermission: "tasks.task.read",
      position: 30,
      descriptionKey: "dashboard.widget_desc.tasks.by_group",
      category: "dashboard.category.tasks",
      size: "md",
      load: (api) =>
        api.GET("/api/v1/tasks/dashboard-groups").then((r) => r.data ?? { items: [], total: 0 }),
      component: TasksByGroupWidget,
    },
  ],
  calendarSources: [
    {
      // Planned task blocks (#188): the viewer's own always, plus any colleagues they overlaid
      // through the per-person feed menu. Timed, so they land on the day/week time grid.
      key: "tasks.scheduled",
      module: "tasks",
      labelKey: "tasks.calendar.scheduled",
      color: "sky",
      load: async (
        api,
        { from, to, user, people, color, personColors, hiddenPeople },
      ): Promise<CalendarEvent[]> => {
        const writeOwn = hasPermission(user?.permissions, "tasks.schedule.write");
        const writeAny = hasPermission(user?.permissions, "tasks.schedule.write", "any");
        const [own, team] = await Promise.all([
          api.GET("/api/v1/tasks/schedules", {
            params: { query: { date_from: from, date_to: to } },
          }),
          people?.length
            ? api.GET("/api/v1/tasks/schedules", {
                params: { query: { date_from: from, date_to: to, user_ids: people } },
              })
            : Promise.resolve(null),
        ]);
        // De-dupe by block id: a colleague the viewer overlaid who is also themselves would
        // otherwise appear twice.
        const byId = new Map<string, NonNullable<typeof own.data>[number]>();
        for (const block of own.data ?? []) byId.set(block.id, block);
        for (const block of team?.data ?? []) byId.set(block.id, block);
        // Colleagues the viewer hid from this split feed drop out entirely (#281).
        const hidden = new Set(hiddenPeople ?? []);
        return [...byId.values()]
          .filter((block) => !block.user_id || !hidden.has(block.user_id))
          .map((block) => {
            const mine = block.user_id === user?.id;
            // Name a colleague's block; the viewer's own feed stays clean (the grid shows when).
            const who = mine ? "" : `${block.user_name ?? ""} · `;
            return {
              id: block.id,
              start: block.start,
              end: block.end,
              title: `${who}${block.task_title}`,
              // Colour precedence (#281): this colleague's own override, then the whole-feed
              // override, then the source default — the same ladder as the leave feeds.
              color: (block.user_id ? personColors?.[block.user_id] : undefined) ?? color ?? "sky",
              href: `/tasks/${block.task_id}`,
              startsAt: block.starts_at,
              endsAt: block.ends_at,
              sourceKey: "tasks.scheduled",
              // Offer the day-drag only where an edit could succeed; the API stays the boundary.
              draggable: mine ? writeOwn : writeAny,
            };
          });
      },
      move: async (api, { id, deltaDays }) => {
        // A day-move: shift the block's local day, keep its time. The API recomputes the instants
        // in the org timezone, so the wall-clock time survives a DST boundary.
        const current = await api.GET("/api/v1/tasks/schedules/{schedule_id}", {
          params: { path: { schedule_id: id } },
        });
        if (current.error) return apiErrorKey(current.error).key;
        const { day } = localDayTime(current.data.starts_at);
        const { error } = await api.PATCH("/api/v1/tasks/schedules/{schedule_id}", {
          params: { path: { schedule_id: id } },
          body: { day: isoAddDays(day, deltaDays) },
        });
        return error ? apiErrorKey(error).key : null;
      },
      people: async (api, { user }): Promise<CalendarPerson[]> => {
        // Only a holder of the any-scope read may overlay colleagues; a member gets no roster.
        if (!hasPermission(user?.permissions, "tasks.schedule.read", "any")) return [];
        const { data } = await api.GET("/api/v1/members/lookup");
        // A colleague who left is not somebody to overlay: the lookup keeps them in the answer
        // on purpose (the picker decides), and this picker decides they are out.
        return (data ?? [])
          .filter((m) => m.is_active)
          .map((m) => ({ id: m.user_id, name: m.full_name || m.email || "" }));
      },
      splitPeople: async (api, { user }): Promise<CalendarPerson[]> => {
        // Per-person colour + show/hide rows (#281), exactly as the leave feeds offer them —
        // this feed draws several colleagues' blocks and drew them all in one colour.
        if (!hasPermission(user?.permissions, "tasks.schedule.read", "any")) return [];
        const { data } = await api.GET("/api/v1/members/lookup");
        return (data ?? [])
          .filter((m) => m.is_active)
          .map((m) => ({ id: m.user_id, name: m.full_name || m.email || "" }));
      },
    },
    {
      // Task deadlines (#188): the viewer's own open tasks with a due date in range, red when
      // overdue. Its own toggleable feed — a deadline is useful on the calendar whether or not
      // the task is scheduled. Not draggable: moving a deadline needs a reason (the task page).
      key: "tasks.due",
      module: "tasks",
      labelKey: "tasks.calendar.deadlines",
      color: "red",
      load: async (api, { from, to, user, color }): Promise<CalendarEvent[]> => {
        if (!user?.id) return [];
        const { data } = await api.GET("/api/v1/tasks", {
          params: {
            query: {
              limit: 200,
              offset: 0,
              meta: false,
              count: false,
              assignee_user_id: user.id,
              due_from: from,
              due_to: to,
            },
          },
        });
        const today = orgToday();
        return (data?.items ?? [])
          .filter((task) => task.due_date && !task.completed_at)
          .map((task) => ({
            id: `due-${task.id}`,
            start: task.due_date!,
            end: task.due_date!,
            title: t("tasks.calendar.deadline", { title: task.title }),
            // A personal override recolours the whole feed; without one, overdue stays red (#281).
            color: color ?? (task.due_date! < today ? "red" : "amber"),
            href: `/tasks/${task.id}`,
          }));
      },
    },
  ],
});
