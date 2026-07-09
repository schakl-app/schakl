import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TASK_COLUMNS, TASKS_TABLE_ID } from "$lib/modules/tasks/columns";

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

  // The saved layout rides in on the layout load, which does not rerun on filter or sort
  // navigation (docs/PERFORMANCE.md). The *server* applies the sort: this page holds 200 of a
  // possibly longer list, and sorting the slice you happen to have sorts the wrong set. The URL
  // wins over the saved default, so a sorted board stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, TASKS_TABLE_ID);
  const resolved = resolveColumns(TASK_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  // Lookups (companies/projects/labels/members) come from the /tasks layout load.
  const { data: tasks } = await api.GET("/api/v1/tasks", {
    params: { query: { limit: 200, offset: 0, sort, ...filters } },
  });

  return {
    tasks: tasks?.items ?? [],
    total: tasks?.total ?? 0,
    filters,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, TASKS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

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
