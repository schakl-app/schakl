import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by every time page (entry form comboboxes, report filters, name display).
 * Deliberately a layout load that never touches the URL: switching day/week only reruns the
 * page load, so these four API calls don't repeat on every tab click.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  /**
   * Unsettled Timeon conflicts, and only for a caller who could act on one (#389).
   *
   * The sync workspace left the main navigation, because a cutover queue is not a top-level
   * destination — it is empty most days and the integration itself ends. What replaces the menu
   * item is this: the queue *finds* the person, beside the hours it is about, on the days it has
   * something to say. Read in the **layout** rather than the page so clicking through a month of
   * weeks does not re-ask, and skipped entirely for a tenant without the integration.
   */
  const timeonEnabled =
    (event.locals.theme?.enabledModules?.includes("timeon") ?? false) &&
    can(event.locals.user, "timeon.sync.run");
  // `event.parent()` is awaited *inside* the Promise.all, never before it: awaiting it first
  // would serialise this whole fan behind the app layout instead of running alongside it
  // (docs/PERFORMANCE.md).
  const [
    companies,
    projects,
    tasks,
    taskStatuses,
    members,
    companyDefs,
    projectDefs,
    timeon,
    parent,
  ] = await Promise.all([
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    // `hours=true` (#112): the budget burn per project rides the lookup this layout already
    // makes — one grouped query server-side, zero extra API calls — so the entry form and the
    // "beschikbare uren" panel can show hours-left for the project being logged against.
    // That includes retainer hours: a project covered by an agreement burns against its
    // included hours (#225), which is why there is no subscription lookup here anymore.
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, count: false, hours: true } },
    }),
    // …and the same for a *task's* hour budget (#313). Same trick, same lookup, still zero
    // extra API calls: the burn rides the list this layout already fetches, so the entry form
    // can say what is left of the picked task before it is saved rather than after. The API
    // omits the two fields for a caller without `time.entry.read`; `meta=false` skips the
    // label/checklist chips the form has no use for, never the budget.
    api.GET("/api/v1/tasks", {
      params: {
        query: { limit: 200, offset: 0, meta: false, count: false, hours: true, sort: "title" },
      },
    }),
    // The tenant's status vocabulary (#62). The task lookup above stays unfiltered because it
    // is also what names the task on every logged row — a finished task's entries must keep
    // their title — so *which of those tasks are still open* is a question only this answers.
    // One small read, in the same flight, in a layout that does not rerun on a day/week click.
    api.GET("/api/v1/tasks/statuses"),
    api.GET("/api/v1/members/lookup"),
    // Custom-field definitions drive the quick-create dialogs (incl. required fields).
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
    // `…/accounts/options` rather than a count of its own: it declares the permission this
    // pointer is gated on, it already answers `open_conflicts` per connection (two grouped
    // queries for the whole list, never one per row), and a second endpoint for one number
    // would be a second place for that number to disagree with the workspace's.
    timeonEnabled ? api.GET("/api/v1/timeon/accounts/options") : Promise.resolve({ data: null }),
    // The personal timesheet view preference (7-day vs Mon–Fri) lives in the same prefs blob
    // the app layout already fetched (#290) — reading it from the parent costs nothing, where
    // a second `GET /prefs` cost a whole authenticated round-trip for a string.
    event.parent(),
  ]);
  const weekView = (parent.prefs as { time?: { week_view?: string } } | undefined)?.time?.week_view;
  return {
    companies: companies.data?.items ?? [],
    projects: projects.data?.items ?? [],
    tasks: tasks.data?.items ?? [],
    taskStatuses: taskStatuses.data ?? [],
    members: members.data ?? [],
    companyDefinitions: companyDefs.data ?? [],
    projectDefinitions: projectDefs.data ?? [],
    weekView: weekView === "work" ? "work" : "full",
    // A number, not a list: what the strip says is "there are three, go and settle them", and
    // the queue itself is a screen. Zero and absent are the same answer here on purpose.
    timeonConflicts: (timeon.data ?? []).reduce((sum, row) => sum + (row.open_conflicts ?? 0), 0),
    locale: event.locals.locale,
  };
};
