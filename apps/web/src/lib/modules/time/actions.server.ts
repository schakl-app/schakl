/**
 * The form actions behind a time-entry ⋯ menu on a *record's* page (#400). SvelteKit actions
 * live on the page, so each host detail page spreads these into its own `actions`.
 *
 * They differ from the copies on `/time`, `/overview` and a project's page in one deliberate
 * way, and it is the whole reason they exist: **they post only the fields the form carried**.
 * Those three hosts mount the full `EntryForm`, which draws the client, project and task
 * pickers, so reading every field with `?? null` is truthful there. A record's panel draws a
 * correction dialog (`EntryQuickEdit`) — the client is a given on that page and the lookups a
 * picker would need are not loaded — so the same read would blank the project and the task of
 * every entry corrected from it.
 *
 * `form.has()` is the question (CLAUDE.md §18: absent means leave alone, an explicit empty
 * value means clear), `undefined` keys vanish in `JSON.stringify`, and the API updates with
 * `exclude_unset`. So a dialog that shows six fields writes six fields.
 *
 * **Host contract:** the panel posts to `?/updateEntry` and `?/deleteEntry`, and the
 * log-hours dialog to `?/createEntry`.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { parsePostedMinutes } from "$lib/core/duration";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

/** A posted field, or `undefined` when the form never rendered it. */
function text(form: FormData, field: string): string | null | undefined {
  return form.has(field) ? String(form.get(field) ?? "").trim() || null : undefined;
}

export const timeEntryActions = {
  /**
   * A whole registration, from a record's own page (#402).
   *
   * Unlike its two neighbours this one *does* read every field with `?? null`, and the
   * difference is the form rather than an inconsistency: `LogTimeDialog` mounts the full
   * `EntryForm`, which draws the client, project and task pickers, so an absent field really
   * does mean "cleared" here. Mirrors `/time`'s own create so an hour logged from a client
   * cannot come out different from one logged on the timesheet.
   */
  createEntry: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!date || !start || !end) return fail(400, { error: "errors.required" });

    const { error: apiError } = await apiFor(event).POST("/api/v1/time/entries", {
      body: {
        // Wall clock, stored as UTC; the API rolls an end that is not after the start forward a
        // day (an overnight span) rather than refusing it.
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: parsePostedMinutes(form.get("break_minutes")) ?? 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        // Stated by the form's toggle; omitted, the project's own default decides (#284) rather
        // than a hardcoded "factureerbaar".
        billable: form.has("billable") ? form.get("billable") !== "false" : undefined,
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { entryCreated: true };
  },

  updateEntry: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!id || !date || !start || !end) return fail(400, { error: "errors.required" });

    // Entry times are the wall clock the user typed, stored as UTC (`modules/time/format.ts`),
    // so the two halves are concatenated rather than converted — the same shape every other
    // host's `updateEntry` posts.
    const { error: apiError } = await apiFor(event).PATCH("/api/v1/time/entries/{entry_id}", {
      params: { path: { entry_id: id } },
      body: {
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: form.has("break_minutes")
          ? (parsePostedMinutes(form.get("break_minutes")) ?? 0)
          : undefined,
        description: text(form, "description"),
        billable: form.has("billable") ? form.get("billable") !== "false" : undefined,
        // Deliberately absent unless posted: see the header comment. An empty *posted* value
        // still clears, which is the other half of the same rule.
        company_id: text(form, "company_id"),
        project_id: text(form, "project_id"),
        task_id: text(form, "task_id"),
        entry_type_key: text(form, "entry_type_key"),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { entryUpdated: true };
  },

  deleteEntry: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      const { error: apiError } = await apiFor(event).DELETE("/api/v1/time/entries/{entry_id}", {
        params: { path: { entry_id: id } },
      });
      if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    }
    return { entryDeleted: true };
  },
};
