import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { editHref } from "$lib/core/edit-intent";
import { apiErrorKey } from "$lib/core/errors";
import { t } from "$lib/core/i18n";
import { impexAction } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TASK_COLUMNS, TASKS_TABLE_ID } from "$lib/modules/tasks/columns";
import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams;
  const filters = {
    company_id: q.get("company_id") || undefined,
    project_id: q.get("project_id") || undefined,
    // "Hangs off no client and no project" — the dashboard tile's own bucket, made addressable
    // (#15). An absent `company_id` means *any* client, so it could never express this.
    unlinked: q.get("unlinked") === "1" || undefined,
    assignee_user_id: q.get("assignee_user_id") || undefined,
    label_id: q.get("label_id") || undefined,
    due: (q.get("due") as "overdue" | "today" | "week" | null) || undefined,
    q: q.get("q") || undefined,
    // "Show me the ones nobody named" (#350). A create-then-edit row that was never finished
    // reads as ordinary work, so without a filter there is no way to find — let alone clear —
    // the ones an interrupted afternoon left behind.
    unnamed: q.get("unnamed") === "1" || undefined,
  };

  // Opening /tasks with no assignee filter shows *your* tasks first, not the whole org's — the
  // person switcher defaults to yourself. `filters.assignee_user_id` stays the raw URL value (so
  // "active filters" counting/the clear-filters link only react to a filter the user actually
  // set); this resolved value is what's sent to the API. Explicitly picking "Geen" writes the
  // `ALL_ASSIGNEES` sentinel rather than deleting the param, which is what lets the user actually
  // reach an unfiltered, every-assignee view instead of snapping back to themselves.
  //
  // That default is a *staff* convenience and it has to say so. `assignee_user_id` means an
  // employee — a client is assigned through `assignee_contact_id`, and `/members/lookup` leaves
  // client memberships out of every assignee picker on purpose — so a portal login is never the
  // assignee of anything. Defaulting to "mine" therefore sent `visible_to_client = true AND
  // assignee_user_id = <the client's own id>` and answered *nothing*, on every load: the client
  // portal's task list was permanently empty however many tasks staff had ticked visible, and the
  // API guarantee it was hiding is tested (`test_portal_sees_only_client_visible_tasks`) by a
  // call that passes no assignee — the one path the browser never takes.
  const isPortal = event.locals.user?.isPortal ?? false;
  const assigneeQuery =
    filters.assignee_user_id === ALL_ASSIGNEES || (isPortal && !filters.assignee_user_id)
      ? undefined
      : (filters.assignee_user_id ?? event.locals.user?.id);

  // The saved layout rides in on the layout load, which does not rerun on filter or sort
  // navigation (docs/PERFORMANCE.md). The *server* applies the sort: this page holds one slice of
  // a possibly longer list, and sorting the slice you happen to have sorts the wrong set. The URL
  // wins over the saved default, so a sorted board stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, TASKS_TABLE_ID);
  const resolved = resolveColumns(TASK_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const paging = resolvePaging(event.url, pref);

  // The hour budget's burn (#313), asked for only by a caller who may read hours — the API
  // omits the two fields for anyone else, so paying for the grouped query would buy nothing.
  //
  // Deliberately *not* also gated on the `allocated` column being visible, which is how the
  // projects list opts into its budget roll-up (docs/UX.md, "a hidden column costs nothing"):
  // here the figure is not only a column. Below `sm` this list is `TaskRow`, which draws the
  // same ⏱ pill and has no column picker anywhere near it, so a visibility gate would hide the
  // burn from exactly the screen that cannot turn it on. One grouped query, on a page that
  // already issues several.
  const hours = can(event.locals.user, "time.entry.read");

  // Lookups (companies/projects/labels/members) come from the /tasks layout load.
  const { data: tasks } = await api.GET("/api/v1/tasks", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        sort,
        hours,
        ...filters,
        assignee_user_id: assigneeQuery,
      },
    },
  });

  return {
    tasks: tasks?.items ?? [],
    total: tasks?.total ?? 0,
    paging,
    filters,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "task"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, TASKS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "task"),
  bulkDelete: (event) => bulkDeleteAction(event, "task"),

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
        // The API requires a non-empty title, so the row still carries one — but it is a
        // placeholder nobody typed, and `unnamed` is what says so (#350). Before the flag, an
        // abandoned create-then-edit row was indistinguishable from real work, and the
        // placeholder was frozen in the *creator's* locale, so one org held both "Naamloze
        // taak" and "Untitled task" and neither was searchable as "the ones nobody named".
        title: t("tasks.untitled"),
        unnamed: true,
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
