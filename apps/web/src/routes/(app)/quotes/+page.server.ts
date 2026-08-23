import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { QUOTE_COLUMNS, QUOTES_TABLE_ID } from "$lib/modules/invoicing/columns";
import { DOCUMENT_FILTERS } from "$lib/modules/invoicing/filters";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.quote.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, QUOTES_TABLE_ID);
  const resolved = resolveColumns(QUOTE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  // The bar and this load read the same keys from the same place, so what the controls show and
  // what the API was asked for cannot disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...DOCUMENT_FILTERS]);
  const statusFilter = filters.status;
  const companyFilter = filters.company;
  const q = filters.q;
  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read; the client picker comes from the section layout, which does
  // not rerun on a filter or sort click (#290).
  const quotes = await api.GET("/api/v1/invoicing/quotes", {
    params: {
      // `lines: false` — the index never draws a line (#290, docs/PERFORMANCE.md).
      query: {
        limit: paging.limit,
        offset: paging.offset,
        sort,
        status: statusFilter,
        company_id: companyFilter,
        q,
        lines: false,
      },
    },
  });

  return {
    quotes: quotes.data?.items ?? [],
    total: quotes.data?.total ?? 0,
    paging,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    statusFilter: statusFilter ?? "",
    companyFilter: companyFilter ?? "",
    q: q ?? "",
    canWrite: can(event.locals.user, "invoicing.quote.write"),
    canInvoices: can(event.locals.user, "invoicing.invoice.read"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, QUOTES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/quotes/{quote_id}", {
      params: { path: { quote_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },
};
