import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { UNINVOICED_COLUMNS, UNINVOICED_TABLE_ID } from "$lib/modules/invoicing/columns";

import type { Actions, PageServerLoad } from "./$types";

const GROUPS = ["day", "week", "month", "year", "company", "project", "user"] as const;
type Group = (typeof GROUPS)[number];

export const load: PageServerLoad = async (event) => {
  // View-only, so the invoice *read* permission is the gate (#277) — mirrored by the API.
  // At `:any` since #266: this is the org's whole unbilled backlog, employee names and
  // hourly rates included, and the key it rides now also opens a client's own invoices.
  if (!can(event.locals.user, "invoicing.invoice.read", "any")) throw redirect(303, "/");
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, UNINVOICED_TABLE_ID);
  const resolved = resolveColumns(UNINVOICED_COLUMNS, pref);
  const raw = event.url.searchParams.get("group") ?? "";
  const group: Group = (GROUPS as readonly string[]).includes(raw) ? (raw as Group) : "company";

  // One call: subtotals and the capped detail arrive together (docs/PERFORMANCE.md).
  const report = await apiFor(event).GET("/api/v1/invoicing/uninvoiced", {
    params: { query: { group } },
  });

  return {
    report: report.data ?? null,
    group,
    table: { pref, widths: resolved.widths },
    canWrite: can(event.locals.user, "invoicing.invoice.write"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, UNINVOICED_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
};
