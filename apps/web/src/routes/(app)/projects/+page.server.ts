import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { editHref } from "$lib/core/edit-intent";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { t } from "$lib/core/i18n";
import { impexAction } from "$lib/core/impex/actions.server";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { HOURS_COLUMN, PROJECT_COLUMNS, PROJECTS_TABLE_ID } from "$lib/modules/projects/columns";
import { PROJECT_STATUS_ALL, PROJECT_WORKING_SET } from "$lib/modules/projects/status";
import { PROJECT_FILTERS } from "$lib/modules/projects/filters";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  // The bar and this load read the same keys from the same place, so what the controls show and
  // what the API was asked for cannot disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...PROJECT_FILTERS]);
  const q = filters.q;
  // "My projects" is filtered by the API (any assignee, not just the primary).
  const mine = filters.mine === "1";
  // Client filter (#154) — applied by the API; the URL keeps it shareable.
  const company_id = filters.company;
  // Projecten opens on the work that is still open — every status except archived — so the URL
  // token and the wire value are not the same string. Absent means the working set; `all` is how
  // "everything, archive included" says so in a URL you can link to; anything else is that one
  // status. Only the resolution lives here: the *set* is in `status.ts`, beside the pills, so the
  // screen and its export cannot end up with two ideas of what is archived. Applied by the API,
  // never in the browser — filtering the fifty rows this page happens to hold would report a
  // total counted over all of them (§9).
  const statusFilter = filters.status ?? "";
  // "Show me the ones nobody named" (#350) — the abandoned create-then-edit rows, which read
  // as ordinary projects and are otherwise ungatherable.
  const unnamed = filters.unnamed === "1" || undefined;
  const status =
    statusFilter === PROJECT_STATUS_ALL ? undefined : statusFilter || PROJECT_WORKING_SET;
  // "Over budget" (#437) — what the dashboard donut's aggregate opens. The API filters on the
  // enriched burn, so the token forces the hours enrichment on: a link arriving with the burn
  // column hidden must not filter on data that was never computed.
  const burn = filters.burn === "over" ? "over" : undefined;

  // The saved layout decides two things before a row is fetched: how the *server* sorts, and
  // whether the budget burn-down is worth computing at all (#24 — a hidden aggregate costs
  // nothing). It comes from the layout load, which doesn't rerun on filter navigation.
  // Must precede the fetch — the saved sort and the hidden-column decision are query parameters
  // of it — and `event.parent()` is memoised, so the section layout's own calls are already in
  // flight by the time this resolves.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, PROJECTS_TABLE_ID);
  const resolved = resolveColumns(PROJECT_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  // A portal login is never drawn the burn column (#449) and must not pay for the aggregate.
  const hours =
    !event.locals.user?.isPortal && resolved.columns.some((column) => column.key === HOURS_COLUMN);

  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read is here; the client picker, the definitions and the member
  // names come from the section layout, which does not rerun on filter/sort navigation (#290).
  const projects = await api.GET("/api/v1/projects", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        q,
        mine,
        sort,
        hours: hours || Boolean(burn),
        company_id,
        status,
        unnamed,
        burn,
      },
    },
  });
  return {
    projects: projects.data?.items ?? [],
    total: projects.data?.total ?? 0,
    paging,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    mine,
    unnamed: unnamed ?? false,
    companyFilter: company_id ?? "",
    // Two values, on purpose: the pills highlight on the *token* the URL carries, and the export
    // sends the *resolved* filter — the whole point of `ImpexBar`'s `filters` is that the file
    // holds what the screen holds, and with a default that narrows, passing the token would let
    // the archived projects quietly back into the spreadsheet.
    statusFilter,
    statusQuery: status ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "project"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, PROJECTS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** The new-project dialog's client picker offers "＋ … toevoegen" like every other (#115). */
  createCompany: createCompanyAction,

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "project"),
  bulkDelete: (event) => bulkDeleteAction(event, "project"),

  /**
   * Create-then-edit (docs/UX.md Principle 3, same as tasks #230): a new project is created
   * minimal — placeholder name, optionally pre-linked to the client the entry point knew — and
   * the user lands on the detail page in edit mode (#78's `?edit=1` marker). No inline creation
   * form duplicates those fields anymore.
   */
  create: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "").trim();
    // The one thing the dialog asks for, and the one thing the placeholder cannot stand in
    // for: a project belongs to a client. Refused here as well as by the API, so the answer
    // is this key rather than the generic validation envelope.
    if (!company_id) return fail(400, { error: "errors.projects_company_required" });
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        // The API requires a non-empty name, so the row still carries one — but it is a
        // placeholder nobody typed, and `unnamed` is what says so (#350; see the twin in
        // `tasks/+page.server.ts`).
        name: t("projects.untitled"),
        unnamed: true,
        company_id,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: true,
        custom: {},
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, editHref(`/projects/${data.id}`));
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/projects/{project_id}", {
        params: { path: { project_id: id } },
      });
    }
    return { deleted: true };
  },
};
