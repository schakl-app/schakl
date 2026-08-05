import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  BACKLOG_COLUMNS,
  BACKLOG_TABLE_ID,
  UNINVOICED_COLUMNS,
  UNINVOICED_TABLE_ID,
} from "$lib/modules/invoicing/columns";

import type { Actions, PageServerLoad } from "./$types";

const HOUR_GROUPS = ["day", "week", "month", "year", "company", "project", "user"] as const;
const BACKLOG_GROUPS = ["company", "month", "source"] as const;
/** Hours, agreements, renewals — the three things an agency still has to invoice, and the
 *  three bands a document already prints them in. `hours` is not a backlog `source`: it is
 *  answered by a different endpoint over a different predicate. */
const SOURCES = ["hours", "subscription", "domain"] as const;

type HourGroup = (typeof HOUR_GROUPS)[number];
type BacklogGroup = (typeof BACKLOG_GROUPS)[number];
type Source = (typeof SOURCES)[number];

function pick<T extends string>(raw: string | null, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(raw ?? "") ? (raw as T) : fallback;
}

export const load: PageServerLoad = async (event) => {
  // View-only, so the invoice *read* permission is the gate (#277) — mirrored by the API.
  // At `:any` since #266: this is the org's whole unbilled backlog, employee names and
  // hourly rates included, and the key it rides now also opens a client's own invoices.
  if (!can(event.locals.user, "invoicing.invoice.read", "any")) throw redirect(303, "/");
  const { prefs } = await event.parent();
  const source = pick<Source>(event.url.searchParams.get("source"), SOURCES, "hours");
  const raw = event.url.searchParams.get("group");
  const group =
    source === "hours"
      ? pick<HourGroup>(raw, HOUR_GROUPS, "company")
      : pick<BacklogGroup>(raw, BACKLOG_GROUPS, "company");

  const tableId = source === "hours" ? UNINVOICED_TABLE_ID : BACKLOG_TABLE_ID;
  const columns = source === "hours" ? UNINVOICED_COLUMNS : BACKLOG_COLUMNS;
  const pref = readTablePref(prefs, tableId);
  const resolved = resolveColumns(columns, pref);

  // Both halves, always, and in parallel: the tiles count all three sources whichever one is
  // detailed below, and a tile that only appeared once you had clicked its tab would not be a
  // summary of anything (docs/UX.md §7). Two calls, never one per source — the backlog's
  // `totals_by_source` covers the other two tiles from the same response that lists one.
  //
  // `source` is passed through, so `groups` and `items` come back already narrowed and the
  // subtotals are the API's own. Filtering a `source=all` response in the browser would have
  // meant re-summing from a **capped** item list, which is exactly the truncated-total the
  // endpoint's cap discipline exists to prevent (docs/PERFORMANCE.md).
  const [hoursReport, backlog] = await Promise.all([
    apiFor(event).GET("/api/v1/invoicing/uninvoiced", {
      params: { query: { group: source === "hours" ? (group as HourGroup) : "company" } },
    }),
    apiFor(event).GET("/api/v1/invoicing/recurring-backlog", {
      params: {
        query: {
          group: source === "hours" ? "company" : (group as BacklogGroup),
          source: source === "hours" ? "all" : source,
        },
      },
    }),
  ]);

  return {
    report: hoursReport.data ?? null,
    backlog: backlog.data ?? null,
    source,
    group,
    table: { id: tableId, pref, widths: resolved.widths },
    canWrite: can(event.locals.user, "invoicing.invoice.write"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    // Hours and the recurring backlog are different tables with different columns, so they
    // keep separate preferences — one shared id would let a renewal layout overwrite the
    // hours one, hiding columns that do not even exist on the other side. Which table is
    // being saved comes from the same `source` the load read: the picker posts to
    // `?/saveTable` on the current URL, so the query string is still here.
    const id =
      pick<Source>(event.url.searchParams.get("source"), SOURCES, "hours") === "hours"
        ? UNINVOICED_TABLE_ID
        : BACKLOG_TABLE_ID;
    await saveTablePref(event, id, parseTablePref(form));
    return { tableSaved: true };
  },
};
