import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";

import type { Actions, PageServerLoad } from "./$types";

const REPORTS_TABLE_ID = "reports";

/**
 * The report register (issue #300).
 *
 * Deliberately one screen for both audiences: the API decides what this caller may see — a
 * client login gets their own published client-facing reports and nothing else, staff without
 * `reporting.internal.read` never see an internal analysis. Filtering by `!isPortal` here would
 * be a second copy of that rule, and the wrong one (docs/UX.md).
 */
export const load: PageServerLoad = async (event) => {
  // Named so the list can re-read itself while any row is still generating; see `poll.svelte.ts`.
  event.depends("reporting:reports");
  const api = apiFor(event);
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, REPORTS_TABLE_ID);
  const paging = resolvePaging(event.url, pref);

  const company_id = event.url.searchParams.get("company") || undefined;
  const audience = event.url.searchParams.get("audience") || undefined;

  const reports = await api.GET("/api/v1/reporting/reports", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        company_id,
        audience: audience as "client" | "internal" | undefined,
      },
    },
  });

  // Only staff who may write reports get the client picker and the batch button; a client has
  // one company and nothing to run.
  const canWrite = can(event.locals.user, "reporting.report.write");
  const companies = canWrite
    ? await api.GET("/api/v1/companies", {
        params: { query: { limit: 200, meta: false, count: false } },
      })
    : null;

  return {
    reports: reports.data?.items ?? [],
    total: reports.data?.total ?? 0,
    paging,
    filters: { company_id: company_id ?? "", audience: audience ?? "" },
    companies: companies?.data?.items?.map((c) => ({ id: c.id, name: c.name })) ?? [],
    canWrite,
    canSend: can(event.locals.user, "reporting.report.send"),
    canSeeInternal: can(event.locals.user, "reporting.internal.read"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Generate one client's report for the current period; the API queues it and returns. */
  generate: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "");
    if (!company_id) return fail(400, { error: "errors.validation" });
    const { error } = await apiFor(event).POST("/api/v1/reporting/reports/generate", {
      body: {
        company_id,
        audience: (String(form.get("audience") ?? "client") || "client") as "client" | "internal",
        // Explicit: a manual run reuses the frozen numbers of an existing report for this
        // period rather than silently re-pricing a month somebody already reviewed.
        refresh_data: false,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { queued: true };
  },

  /**
   * Run the whole book of clients for the period — the "it is the fifth" button.
   *
   * The API queues one job per client, so a client whose data source is down fails alone. It
   * answers with what it skipped and why, which is the half a fire-and-forget batch never told
   * anybody.
   */
  generateAll: async (event) => {
    const form = await event.request.formData();
    const { data, error } = await apiFor(event).POST("/api/v1/reporting/reports/generate-batch", {
      body: {
        audience: (String(form.get("audience") ?? "client") || "client") as "client" | "internal",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return {
      queued: true,
      batch: {
        queued: data?.queued ?? 0,
        skipped: data?.skipped ?? [],
        enrolled: data?.enrolled ?? 0,
        unconfigured: data?.unconfigured ?? 0,
      },
    };
  },
};
