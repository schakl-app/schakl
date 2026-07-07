import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [tasks, companies] = await Promise.all([
    api.GET("/api/v1/tasks", { params: { query: { limit: 100, offset: 0 } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
  ]);
  return {
    tasks: tasks.data?.items ?? [],
    total: tasks.data?.total ?? 0,
    companies: companies.data?.items ?? [],
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { error: "errors.required" });

    const company_id = String(form.get("company_id") ?? "").trim();
    const assignee = String(form.get("assignee_user_id") ?? "").trim();
    const due_date = String(form.get("due_date") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        description: String(form.get("description") ?? "").trim() || null,
        status: "open",
        priority: (String(form.get("priority") ?? "normal") as "low" | "normal" | "high"),
        company_id: company_id || null,
        assignee_user_id: assignee || null,
        due_date: due_date || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const status = String(form.get("status") ?? "done") as "open" | "in_progress" | "done";
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
