import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/** ISO date (YYYY-MM-DD) of the Monday on or before `d`. */
function weekStartOf(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  const day = d.getUTCDay(); // 0=Sun..6=Sat
  const diff = (day + 6) % 7; // days since Monday
  d.setUTCDate(d.getUTCDate() - diff);
  return d.toISOString().slice(0, 10);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const selectedDate = event.url.searchParams.get("date") || todayIso();
  const week_start = event.url.searchParams.get("week") || weekStartOf(selectedDate);

  const [timer, week, day, companies, projects, tasks] = await Promise.all([
    api.GET("/api/v1/time/timer"),
    api.GET("/api/v1/time/timesheet", { params: { query: { week_start } } }),
    api.GET("/api/v1/time/day", { params: { query: { date: selectedDate } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/tasks", { params: { query: { limit: 200, offset: 0 } } }),
  ]);

  return {
    running: timer.data ?? null,
    week: week.data ?? null,
    day: day.data ?? null,
    selectedDate,
    week_start,
    today: todayIso(),
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
  };
};

export const actions: Actions = {
  startTimer: async (event) => {
    const form = await event.request.formData();
    await apiFor(event).POST("/api/v1/time/timer/start", {
      body: {
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        break_minutes: 0,
      },
    });
    return { started: true };
  },

  stopTimer: async (event) => {
    await apiFor(event).POST("/api/v1/time/timer/stop", {});
    return { stopped: true };
  },

  createEntry: async (event) => {
    const form = await event.request.formData();
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!date || !start || !end) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/time/entries", {
      body: {
        // Times are entered + stored as wall-clock (as UTC); the API rolls the end forward a day
        // if it isn't after the start (overnight spans).
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: Number(form.get("break_minutes") ?? 0) || 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  deleteEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/time/entries/{entry_id}", {
        params: { path: { entry_id: id } },
      });
    }
    return { deleted: true };
  },
};
