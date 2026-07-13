import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { TIME_REPORT_COLUMNS, TIME_REPORT_TABLE_ID } from "$lib/modules/time/columns";

import type { Actions, PageServerLoad } from "./$types";

/** The status pill maps to the report's approved/invoiced/billable flags. */
function statusFlags(status: string): {
  approved?: boolean;
  invoiced?: boolean;
  billable?: boolean;
} {
  switch (status) {
    case "open":
      return { approved: false };
    case "approved":
      return { approved: true };
    case "to_invoice":
      return { approved: true, invoiced: false, billable: true };
    case "invoiced":
      return { invoiced: true };
    default:
      return {};
  }
}

function monthStartIso(): string {
  return new Date().toISOString().slice(0, 8) + "01";
}

export const load: PageServerLoad = async (event) => {
  // Manager gate + lookups live in the /overview layout load.
  const api = apiFor(event);
  const q = event.url.searchParams;
  const filters = {
    user_id: q.get("user_id") || "",
    company_id: q.get("company_id") || "",
    project_id: q.get("project_id") || "",
    date_from: q.get("date_from") ?? monthStartIso(),
    date_to: q.get("date_to") ?? "",
    status: q.get("status") ?? "",
    entry_type: q.get("entry_type") || "",
  };

  // The saved layout comes from the /overview layout load, which does not rerun on filter or sort
  // navigation. The *server* sorts: this page holds 500 rows of a possibly much longer set, and
  // the totals below it describe the whole set, not the page.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, TIME_REPORT_TABLE_ID);
  const resolved = resolveColumns(TIME_REPORT_COLUMNS, pref);
  const sort = q.get("sort") ?? resolved.sort ?? undefined;

  const { data: report } = await api.GET("/api/v1/time/report", {
    params: {
      query: {
        limit: 500,
        offset: 0,
        user_id: filters.user_id || undefined,
        company_id: filters.company_id || undefined,
        project_id: filters.project_id || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
        entry_type: filters.entry_type || undefined,
        sort,
        ...statusFlags(filters.status),
      },
    },
  });

  return {
    report: report ?? null,
    filters,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
  };
};

async function bulk(
  event: Parameters<NonNullable<Actions[string]>>[0],
  path: "/api/v1/time/entries/approve" | "/api/v1/time/entries/invoice",
  flag: "approved" | "invoiced",
  value: boolean,
) {
  const form = await event.request.formData();
  const entry_ids = String(form.get("entry_ids") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (entry_ids.length === 0) return fail(400, { error: "errors.required" });
  const { error } = await apiFor(event).POST(path, {
    body: { entry_ids, [flag]: value } as never,
  });
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { updated: entry_ids.length };
}

export const actions: Actions = {
  /** Persist this manager's column layout. Personal, in-view (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, TIME_REPORT_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  approve: (event) => bulk(event, "/api/v1/time/entries/approve", "approved", true),
  unapprove: (event) => bulk(event, "/api/v1/time/entries/approve", "approved", false),
  invoice: (event) => bulk(event, "/api/v1/time/entries/invoice", "invoiced", true),
  uninvoice: (event) => bulk(event, "/api/v1/time/entries/invoice", "invoiced", false),

  // Edit/delete a single entry straight from the report (managers may edit others' and
  // approved entries — the API enforces the role rules). Mirrors the /time page actions.
  updateEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const date = String(form.get("date") ?? "").trim();
    const start = String(form.get("start") ?? "").trim();
    const end = String(form.get("end") ?? "").trim();
    if (!id || !date || !start || !end) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).PATCH("/api/v1/time/entries/{entry_id}", {
      params: { path: { entry_id: id } },
      body: {
        started_at: `${date}T${start}:00Z`,
        ended_at: `${date}T${end}:00Z`,
        break_minutes: Number(form.get("break_minutes") ?? 0) || 0,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        project_id: String(form.get("project_id") ?? "").trim() || null,
        task_id: String(form.get("task_id") ?? "").trim() || null,
        billable: form.get("billable") !== "false",
        entry_type_key: String(form.get("entry_type_key") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { updated: 1 };
  },

  deleteEntry: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/time/entries/{entry_id}", {
        params: { path: { entry_id: id } },
      });
    }
    return { deleted: true };
  },
};
