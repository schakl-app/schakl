import { fail, redirect } from "@sveltejs/kit";

import { editHref } from "$lib/core/edit-intent";
import { apiErrorKey } from "$lib/core/errors";
import { t } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TASK_COLUMNS, TASKS_TABLE_ID } from "$lib/modules/tasks/columns";

import type { Actions, PageServerLoad } from "./$types";

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

  /**
   * Create-then-edit (#230, docs/UX.md Principle 3): a new task is created minimal —
   * placeholder title, assigned to its creator, optionally pre-linked to the client/project
   * the entry point knew — and the user lands on the detail page in edit mode (#78's
   * `?edit=1` marker), the one surface where a task's definition is edited. No inline
   * creation form duplicates those fields anymore.
   */
  create: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        // The API requires a non-empty title; the placeholder is stored in the creator's
        // locale and replaced the moment they type a real one on the detail page.
        title: t("tasks.untitled"),
        // Status is omitted so the API assigns the org's default status (issue #62).
        priority: "normal",
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        assignee_user_id: event.locals.user?.id ?? null,
        // New tasks don't demand a closing contact moment; toggled later on the task page (#157).
        requires_interaction: false,
        visible_to_client: false,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, editHref(`/tasks/${data.id}`));
  },

  toggle: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    // A configured status key (issue #62); the row computes which one from the org's vocabulary.
    const status = String(form.get("status") ?? "").trim();
    if (id && status) {
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
