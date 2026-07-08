import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

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
  const [projects, companies, definitions, members] = await Promise.all([
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, q } } }),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
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
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const company_id = String(form.get("company_id") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        description: String(form.get("description") ?? "").trim() || null,
        company_id: company_id || null,
        responsible_user_id: String(form.get("responsible_user_id") ?? "") || null,
        status: String(form.get("status") ?? "active") as "active",
        budget_period: "total",
        currency: "EUR",
        billable_default: form.get("billable_default") === "on",
        budget_hours: numberOrNull(form.get("budget_hours")),
        budget_amount: numberOrNull(form.get("budget_amount")),
        hourly_rate: numberOrNull(form.get("hourly_rate")),
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
