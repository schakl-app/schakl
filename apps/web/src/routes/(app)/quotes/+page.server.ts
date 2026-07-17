import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { QUOTE_COLUMNS, QUOTES_TABLE_ID } from "$lib/modules/invoicing/columns";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.quote.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, QUOTES_TABLE_ID);
  const resolved = resolveColumns(QUOTE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const statusFilter = event.url.searchParams.get("status") ?? undefined;
  const companyFilter = event.url.searchParams.get("company") ?? undefined;
  const q = event.url.searchParams.get("q") ?? undefined;

  const [quotes, companies] = await Promise.all([
    api.GET("/api/v1/invoicing/quotes", {
      params: {
        query: { limit: 200, offset: 0, sort, status: statusFilter, company_id: companyFilter, q },
      },
    }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, count: false } } }),
  ]);

  return {
    quotes: quotes.data?.items ?? [],
    total: quotes.data?.total ?? 0,
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
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
