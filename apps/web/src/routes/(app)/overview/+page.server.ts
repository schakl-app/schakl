import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

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
  };

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
        ...statusFlags(filters.status),
      },
    },
  });

  return {
    report: report ?? null,
    filters,
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
  approve: (event) => bulk(event, "/api/v1/time/entries/approve", "approved", true),
  unapprove: (event) => bulk(event, "/api/v1/time/entries/approve", "approved", false),
  invoice: (event) => bulk(event, "/api/v1/time/entries/invoice", "invoiced", true),
  uninvoice: (event) => bulk(event, "/api/v1/time/entries/invoice", "invoiced", false),
};
