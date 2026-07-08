import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function numberOrNull(raw: FormDataEntryValue | null): number | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const project_id = event.params.id;

  const { data: project } = await api.GET("/api/v1/projects/{project_id}", {
    params: { path: { project_id } },
  });
  if (!project) throw error(404, { code: "not_found", message: "errors.not_found" });

  // Periodic budgets burn down against the current period only; "total" against all time.
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = today.slice(0, 8) + "01";
  const now = new Date(today + "T00:00:00Z");
  now.setUTCDate(now.getUTCDate() - ((now.getUTCDay() + 6) % 7)); // Monday of this week
  const weekStart = now.toISOString().slice(0, 10);
  const periodStart =
    project.budget_period === "monthly"
      ? monthStart
      : project.budget_period === "weekly"
        ? weekStart
        : project.budget_period === "daily"
          ? today
          : null;
  const [tasks, companies, logged, members, definitions] = await Promise.all([
    api.GET("/api/v1/tasks", { params: { query: { project_id, limit: 200, offset: 0 } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/time/logged", {
      params: {
        query: periodStart
          ? { project_id, date_from: periodStart, date_to: today }
          : { project_id },
      },
    }),
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
  ]);

  return {
    project,
    tasks: tasks.data?.items ?? [],
    companies: companies.data?.items ?? [],
    logged: logged.data ?? { minutes: 0, billable_minutes: 0 },
    members: members.data ?? [],
    definitions: definitions.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/projects/{project_id}", {
      params: { path: { project_id: event.params.id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        description: String(form.get("description") ?? "").trim() || null,
        responsible_user_id: String(form.get("responsible_user_id") ?? "") || null,
        status: String(form.get("status") ?? "active") as "active",
        billable_default: form.get("billable_default") === "on",
        budget_period: String(form.get("budget_period") ?? "total") as "total",
        budget_hours: numberOrNull(form.get("budget_hours")),
        budget_amount: numberOrNull(form.get("budget_amount")),
        hourly_rate: numberOrNull(form.get("hourly_rate")),
        start_date: String(form.get("start_date") ?? "").trim() || null,
        end_date: String(form.get("end_date") ?? "").trim() || null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { updated: true };
  },

  addTask: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { error: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { error: apiError } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        status: "open",
        priority: "normal",
        project_id: event.params.id,
        company_id: company_id || null,
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { taskAdded: true };
  },

  reorderTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const position = Number(form.get("position"));
    if (id && Number.isFinite(position)) {
      await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { position },
      });
    }
    return { taskReordered: true };
  },

  toggleTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const status = String(form.get("status") ?? "done") as "open" | "in_progress" | "done";
    if (id) {
      await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { status },
      });
    }
    return { taskToggled: true };
  },

  deleteTask: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
      });
    }
    return { taskDeleted: true };
  },

  deleteProject: async (event) => {
    await apiFor(event).DELETE("/api/v1/projects/{project_id}", {
      params: { path: { project_id: event.params.id } },
    });
    throw redirect(303, "/projects");
  },
};
