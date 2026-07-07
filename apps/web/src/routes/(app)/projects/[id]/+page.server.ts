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

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const project_id = event.params.id;

  const { data: project } = await api.GET("/api/v1/projects/{project_id}", {
    params: { path: { project_id } },
  });
  if (!project) throw error(404, { code: "not_found", message: "errors.not_found" });

  const [tasks, companies, logged] = await Promise.all([
    api.GET("/api/v1/tasks", { params: { query: { project_id, limit: 200, offset: 0 } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/time/logged", { params: { query: { project_id } } }),
  ]);

  return {
    project,
    tasks: tasks.data?.items ?? [],
    companies: companies.data?.items ?? [],
    logged: logged.data ?? { minutes: 0, billable_minutes: 0 },
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/projects/{project_id}", {
      params: { path: { project_id: event.params.id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        status: String(form.get("status") ?? "active") as "active",
        billable_default: form.get("billable_default") === "on",
        budget_hours: numberOrNull(form.get("budget_hours")),
        budget_amount: numberOrNull(form.get("budget_amount")),
        hourly_rate: numberOrNull(form.get("hourly_rate")),
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
