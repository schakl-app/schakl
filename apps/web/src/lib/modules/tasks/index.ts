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
import { SquareCheckBig } from "@lucide/svelte";

import { localDayTime } from "./schedule";
import TasksPanel from "./TasksPanel.svelte";
import MyTasksWidget from "./MyTasksWidget.svelte";
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
    },
  ],
  dashboardWidgets: [
    {
      key: "tasks.my_open",
      module: "tasks",
      position: 20,
      requiresPermission: "tasks.task.read",
      descriptionKey: "dashboard.widget_desc.tasks.my_open",
      category: "dashboard.category.tasks",
      size: "md",
      load: (api) => api.GET("/api/v1/tasks/mine").then((r) => r.data ?? []),
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
      load: async (api) => {
        // Grouping needs only ids/names — skip the per-task aggregates (`meta`) and the
        // discarded COUNT (`count`) on every list (see docs/PERFORMANCE.md).
        const [tasks, projects, companies] = await Promise.all([
          api.GET("/api/v1/tasks", {
            params: { query: { limit: 200, offset: 0, meta: false, count: false } },
          }),
          api.GET("/api/v1/projects", {
            params: { query: { limit: 200, offset: 0, count: false } },
          }),
          api.GET("/api/v1/companies", {
            params: { query: { limit: 200, offset: 0, count: false } },
          }),
        ]);
        return {
          tasks: tasks.data?.items ?? [],
          projects: projects.data?.items ?? [],
          companies: companies.data?.items ?? [],
        };
      },
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
      load: async (api, { from, to, user, people }): Promise<CalendarEvent[]> => {
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
        return [...byId.values()].map((block) => {
          const mine = block.user_id === user?.id;
          // Name a colleague's block; the viewer's own feed stays clean (the time grid shows when).
          const who = mine ? "" : `${block.user_name ?? ""} · `;
          return {
            id: block.id,
            start: block.start,
            end: block.end,
            title: `${who}${block.task_title}`,
            color: "sky",
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
        return (data ?? []).map((m) => ({ id: m.user_id, name: m.full_name || m.email }));
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
      load: async (api, { from, to, user }): Promise<CalendarEvent[]> => {
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
        const today = new Date().toISOString().slice(0, 10);
        return (data?.items ?? [])
          .filter((task) => task.due_date && !task.completed_at)
          .map((task) => ({
            id: `due-${task.id}`,
            start: task.due_date!,
            end: task.due_date!,
            title: t("tasks.calendar.deadline", { title: task.title }),
            color: task.due_date! < today ? "red" : "amber",
            href: `/tasks/${task.id}`,
          }));
      },
    },
  ],
});
