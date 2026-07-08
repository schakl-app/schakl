import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

type TaskStatus = "open" | "in_progress" | "done";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams;
  const filters = {
    company_id: q.get("company_id") || undefined,
    project_id: q.get("project_id") || undefined,
    assignee_user_id: q.get("assignee_user_id") || undefined,
    label_id: q.get("label_id") || undefined,
    due: (q.get("due") as "overdue" | "today" | "week" | null) || undefined,
    q: q.get("q") || undefined,
  };

  // Lookups (companies/projects/labels/members) come from the /tasks layout load.
  const { data: tasks } = await api.GET("/api/v1/tasks", {
    params: { query: { limit: 200, offset: 0, ...filters } },
  });

  return {
    tasks: tasks?.items ?? [],
    total: tasks?.total ?? 0,
    filters,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        description: String(form.get("description") ?? "").trim() || null,
        status: "open",
        priority: String(form.get("priority") ?? "normal") as "low" | "normal" | "high",
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        assignee_user_id: String(form.get("assignee_user_id") ?? "").trim() || null,
        due_date: String(form.get("due_date") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const status = String(form.get("status") ?? "done") as TaskStatus;
    if (id) {
      await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body: { status },
      });
    }
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
      });
    }
    return { deleted: true };
  },
};
