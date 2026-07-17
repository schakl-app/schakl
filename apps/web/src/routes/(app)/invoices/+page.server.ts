import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { INVOICE_COLUMNS, INVOICES_TABLE_ID } from "$lib/modules/invoicing/columns";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.invoice.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, INVOICES_TABLE_ID);
  const resolved = resolveColumns(INVOICE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const statusFilter = event.url.searchParams.get("status") ?? undefined;
  const companyFilter = event.url.searchParams.get("company") ?? undefined;
  const overdue = event.url.searchParams.get("overdue") === "1";
  const q = event.url.searchParams.get("q") ?? undefined;

  const [invoices, summary, companies] = await Promise.all([
    api.GET("/api/v1/invoicing/invoices", {
      params: {
        query: {
          limit: 200,
          offset: 0,
          sort,
          status: statusFilter,
          company_id: companyFilter,
          overdue,
          q,
        },
      },
    }),
    api.GET("/api/v1/invoicing/summary"),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, count: false } } }),
  ]);

  return {
    invoices: invoices.data?.items ?? [],
    total: invoices.data?.total ?? 0,
    summary: summary.data ?? null,
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    statusFilter: statusFilter ?? "",
    companyFilter: companyFilter ?? "",
    overdueFilter: overdue,
    q: q ?? "",
    canWrite: can(event.locals.user, "invoicing.invoice.write"),
    canQuotes: can(event.locals.user, "invoicing.quote.read"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  // Invoice unbilled hours (owner request): the API's from-time bridge, finally reachable.
  fromTime: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "");
    if (!company_id) return fail(400, { fromTimeError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/invoicing/invoices/from-time", {
      body: {
        company_id,
        until: String(form.get("until") ?? "").trim() || null,
        group_by: (String(form.get("group_by") ?? "") || "project") as "entry" | "day" | "project",
      },
    });
    if (error || !data) return fail(400, { fromTimeError: apiErrorKey(error).key });
    throw redirect(303, `/invoices/${data.id}`);
  },

  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, INVOICES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
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
