import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { INVOICE_COLUMNS, INVOICES_TABLE_ID } from "$lib/modules/invoicing/columns";
import { INVOICE_FILTERS } from "$lib/modules/invoicing/filters";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.invoice.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, INVOICES_TABLE_ID);
  const resolved = resolveColumns(INVOICE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  // The bar and this load read the same keys from the same place, so what the controls show and
  // what the API was asked for cannot disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...INVOICE_FILTERS]);
  const statusFilter = filters.status;
  const companyFilter = filters.company;
  const overdue = filters.overdue === "1";
  const q = filters.q;
  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read; the tiles and the client picker come from the section layout,
  // which does not rerun on a filter or sort click (#290).
  const invoices = await api.GET("/api/v1/invoicing/invoices", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        sort,
        status: statusFilter,
        company_id: companyFilter,
        overdue,
        q,
        // The index draws number, client, date, status and total — never a line. Loading
        // every line of a whole page of invoices to derive tax groups nobody renders was the
        // heaviest thing this response did (#290, docs/PERFORMANCE.md).
        lines: false,
      },
    },
  });

  return {
    invoices: invoices.data?.items ?? [],
    total: invoices.data?.total ?? 0,
    paging,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    statusFilter: statusFilter ?? "",
    companyFilter: companyFilter ?? "",
    overdueFilter: overdue,
    q: q ?? "",
    canWrite: can(event.locals.user, "invoicing.invoice.write"),
    canQuotes: can(event.locals.user, "invoicing.quote.read"),
    /**
     * Does this viewer read the invoice *register*, or only their own documents (#266)?
     *
     * `:any` is the agency's view of the section — the draft tile and its filter chip, the
     * client picker, the column picker. An `:own` holder (a client portal login) gets the
     * same route drawing only what its API answers: their issued invoices. The gate is the
     * API's own key and scope, never `!isPortal` — which mirrors the API less precisely and
     * would still show all of it to a restricted staff member (docs/UX.md).
     */
    canReadRegister: can(event.locals.user, "invoicing.invoice.read", "any"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, INVOICES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /**
   * Bulk delete, the one generic action an invoice takes — there is nothing on one a selection
   * could sensibly share, and every status move is its own endpoint with its own rules. The API
   * allows drafts only and reports the rest per row.
   */
  bulkDelete: (event) => bulkDeleteAction(event, "invoice"),
  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
