import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { editHref } from "$lib/core/edit-intent";
import { apiErrorKey } from "$lib/core/errors";
import { impexAction } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TASK_COLUMNS, TASKS_TABLE_ID } from "$lib/modules/tasks/columns";
import { taskCreateBody } from "$lib/modules/tasks/create";
import { ALL_ASSIGNEES } from "$lib/modules/tasks/filters";
import { DUE_SORT, resolveGrouping } from "$lib/modules/tasks/grouping";

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
    // "Show me the ones with no deadline" (#392). The rule arrived with the column still
    // nullable (expand/contract, docs/WORKFLOW.md), so an instance upgrades carrying rows the
    // new rule forbids — and a list sorted by a date they do not have is not a way to find
    // them. With the ✎ bulk edit beside it, this is how a whole backlog gets dated in one go.
    undated: q.get("undated") === "1" || undefined,
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
  // What the board *groups* by (#395), and what it therefore asks the API to order by when
  // nobody has said otherwise. Grouped by deadline it asks for `sort=due` — the composite
  // "deadline first, then priority" the team's sentence describes — because without it every
  // task sharing a date falls back to `position`, the hand-dragged board order, and the one
  // Friday task that cannot slip sits wherever it was last put. Grouped by status it keeps the
  // dragged order, which is what a status board is for.
  //
  // Three layers, and the precedence matters: an explicit `?sort=` wins over the saved layout,
  // which wins over the grouping's own default. A sort the user asked for orders *within* the
  // sections and never reshuffles them (#38).
  const grouping = resolveGrouping(event.url.searchParams.get("group"));
  const groupingSort = grouping === "due" ? DUE_SORT : undefined;
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? groupingSort;
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
    grouping,
    // The *explicit* sort only: the grouping's own default is not something the user picked, so
    // the column picker must not draw it as a sort in force and clicking a header must not have
    // to un-pick it first.
    table: {
      pref,
      sort: event.url.searchParams.get("sort") ?? resolved.sort ?? null,
      widths: resolved.widths,
    },
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
   * The one create path behind every "＋ nieuwe taak" (#391) — this list's button, the client's
   * Taken panel, the client header. The user names the task in `TaskQuickCreate` (the dialog
   * every picker's inline-create already opens) and *then* lands on the detail page in edit
   * mode (#78's `?edit=1` marker), so create-then-edit's benefit survives: one editing surface,
   * no second field set to keep in step.
   *
   * What no longer survives is the row this action used to write before anyone had been asked
   * anything — a placeholder title marked `unnamed` (#350), a due date of nothing and an
   * assignee it picked itself, left on the board by one click and a closed tab.
   */
  create: async (event) => {
    const form = await event.request.formData();
    const body = taskCreateBody(form, { fallbackAssigneeUserId: event.locals.user?.id ?? null });
    if (!body) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/tasks", { body });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, editHref(`/tasks/${data.id}`));
  },

  /**
   * Create the task a colleague just dictated (#382).
   *
   * Deliberately **not** create-then-edit. That shape exists because a typed task starts as a
   * title and gets its fields on the detail page; this one arrives with all of them already
   * reviewed on screen, so landing the user in edit mode over it would be the second review of
   * the same draft.
   *
   * One API call, carrying the checklist, the links and the labels (`TaskCreate`'s composite
   * fields, #382). The draft rides as one JSON field because it is nested — steps and links —
   * and flat form inputs cannot express that without inventing a naming convention this action
   * would then have to re-parse.
   */
  createDictated: async (event) => {
    const form = await event.request.formData();
    let draft: Record<string, unknown>;
    try {
      draft = JSON.parse(String(form.get("payload") ?? "{}"));
    } catch {
      return fail(400, { error: "errors.validation" });
    }
    const title = String(draft.title ?? "").trim();
    if (!title) return fail(400, { error: "errors.validation" });
    // Required (#382 meets #392): the sheet asks for it and the speaker reviews it, so an
    // empty one here is a client that did not — refused rather than defaulted, because a
    // human is watching and a date nobody chose is what this whole issue is about.
    const dictatedDue = String(draft.due_date ?? "").trim();
    if (!dictatedDue) return fail(400, { error: "errors.required" });

    const steps = (Array.isArray(draft.checklist_items) ? draft.checklist_items : []) as {
      title?: string;
      description?: string | null;
    }[];
    const links = (Array.isArray(draft.links) ? draft.links : []) as {
      url?: string;
      title?: string | null;
    }[];
    const labelIds = (Array.isArray(draft.label_ids) ? draft.label_ids : []) as string[];
    const checklistTitle = (draft.checklist_title as string | null) || null;
    const { data, error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        description: (draft.description as string | null) || null,
        due_date: dictatedDue,
        priority: ((draft.priority as "low" | "normal" | "high" | null) ?? "normal") as
          "low" | "normal" | "high",
        status: (draft.status as string | null) || null,
        company_id: (draft.company_id as string | null) || null,
        project_id: (draft.project_id as string | null) || null,
        assignee_user_id: (draft.assignee_user_id as string | null) || null,
        allocated_minutes: (draft.allocated_minutes as number | null) ?? null,
        // The two flags are tri-state on the draft and plain booleans on the wire. Resolving
        // them here rather than in the browser keeps `null` meaning "the speaker said nothing",
        // which is what stops a `false` reading as a decision nobody made (#284).
        requires_interaction: draft.requires_interaction === true,
        visible_to_client: draft.visible_to_client === true,
        ...(steps.length || checklistTitle
          ? {
              checklist: {
                title: checklistTitle,
                items: steps.map((s) => ({
                  title: String(s.title ?? "").trim(),
                  description: s.description ?? null,
                })),
              },
            }
          : {}),
        links: links.map((l) => ({ url: String(l.url ?? "").trim(), title: l.title ?? null })),
        label_ids: labelIds.map(String),
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // Straight to the finished task, in *use* mode: the review already happened.
    throw redirect(303, `/tasks/${data.id}`);
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
