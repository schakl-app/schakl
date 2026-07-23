import { fail } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { HOURS_COLUMN, PROJECT_COLUMNS, PROJECTS_TABLE_ID } from "$lib/modules/projects/columns";

import type { Actions, PageServerLoad } from "./$types";

function numberOrNull(raw: FormDataEntryValue | null): number | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams.get("q") || undefined;
  // "My projects" is filtered by the API (any assignee, not just the primary).
  const mine = event.url.searchParams.get("mine") === "1";
  // Client filter (#154) — applied by the API; the URL keeps it shareable.
  const company_id = event.url.searchParams.get("company") || undefined;

  // The saved layout decides two things before a row is fetched: how the *server* sorts, and
  // whether the budget burn-down is worth computing at all (#24 — a hidden aggregate costs
  // nothing). It comes from the layout load, which doesn't rerun on filter navigation.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, PROJECTS_TABLE_ID);
  const resolved = resolveColumns(PROJECT_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const hours = resolved.columns.some((column) => column.key === HOURS_COLUMN);

  const [projects, companies, definitions, members] = await Promise.all([
    api.GET("/api/v1/projects", {
      params: { query: { limit: 200, offset: 0, q, mine, sort, hours, company_id } },
    }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "project" } },
    }),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    projects: projects.data?.items ?? [],
    total: projects.data?.total ?? 0,
    companies: companies.data?.items ?? [],
    definitions: definitions.data ?? [],
    members: members.data ?? [],
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    mine,
    companyFilter: company_id ?? "",
    locale: event.locals.locale,
  };
};

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, PROJECTS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const company_id = String(form.get("company_id") ?? "").trim();
    // An empty picker means "didn't say", not "nobody": send no roster at all so the API can
    // inherit the client's verantwoordelijke, which is what the field's placeholder promises.
    const assignees = parseAssignees(form.get("assignees"));
    const { error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: company_id || null,
        assignees: assignees?.length ? assignees : undefined,
        status: String(form.get("status") ?? "active") as "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: form.get("billable_default") !== null,
        budget_hours: numberOrNull(form.get("budget_hours")),
        budget_amount: numberOrNull(form.get("budget_amount")),
        start_date: String(form.get("start_date") ?? "").trim() || null,
        end_date: String(form.get("end_date") ?? "").trim() || null,
        color: String(form.get("color") ?? "").trim() || null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { created: true };
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
