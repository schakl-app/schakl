import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

/** The modal posts its linked projects as one JSON field (single-save surface). */
function parseLinks(raw: FormDataEntryValue | null): { entity_type: "project"; entity_id: string }[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((l) => l && l.entity_type === "project" && typeof l.entity_id === "string")
      .map((l) => ({ entity_type: "project" as const, entity_id: l.entity_id }));
  } catch {
    return [];
  }
}

/** The recurring-agreement fields both actions share (#30). */
function subscriptionBody(form: FormData) {
  const amount = String(form.get("amount") ?? "").trim();
  return {
    name: String(form.get("name") ?? "").trim(),
    status: String(form.get("status") ?? "draft") as "draft",
    interval: String(form.get("interval") ?? "monthly") as "monthly",
    start_date: String(form.get("start_date") ?? "").trim(),
    end_date: String(form.get("end_date") ?? "").trim() || null,
    next_invoice_date: String(form.get("next_invoice_date") ?? "").trim() || null,
    included_hours: String(form.get("included_hours") ?? "").trim() || null,
    notes: String(form.get("notes") ?? "").trim() || null,
    amount: amount || undefined,
    custom: parseCustom(form.get("custom")),
    links: parseLinks(form.get("links")),
  };
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "subscriptions.subscription.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const sort = event.url.searchParams.get("sort") ?? undefined;

  const [subscriptions, summary, companies, projects, definitions, companyDefinitions] =
    await Promise.all([
    api.GET("/api/v1/subscriptions", { params: { query: { limit: 200, offset: 0, sort } } }),
    api.GET("/api/v1/subscriptions/summary"),
    api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/projects", { params: { query: { limit: 200, offset: 0, count: false } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "subscription" } },
    }),
    // For the inline company quick-create (#115): the full dialog includes custom fields.
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
  ]);

  return {
    subscriptions: subscriptions.data?.items ?? [],
    total: subscriptions.data?.total ?? 0,
    summary: summary.data ?? null,
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    projects: lookupItems(projects, "projects").map((p) => ({ id: p.id, name: p.name })),
    definitions: definitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const body = subscriptionBody(form);
    const company_id = String(form.get("company_id") ?? "");
    if (!body.name || !company_id || !body.start_date || body.amount === undefined) {
      return fail(400, { error: "errors.required" });
    }
    const { error } = await apiFor(event).POST("/api/v1/subscriptions", {
      body: { ...body, company_id, amount: body.amount } as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { created: true };
  },

  update: async (event) => {
    const form = await event.request.formData();
    const subscription_id = String(form.get("id") ?? "");
    const body = subscriptionBody(form);
    const company_id = String(form.get("company_id") ?? "");
    if (!subscription_id || !body.name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/subscriptions/{subscription_id}", {
      params: { path: { subscription_id } },
      body: { ...body, company_id: company_id || undefined } as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  /** Inline project create from the links picker (docs/UX.md — per-picker definition of
   *  done). Returns `inlineCreated` so the modal auto-selects the new project as a link. */
  createProject: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { qcError: "errors.required" });
    const rate = Number(String(form.get("hourly_rate") ?? "").trim());
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: true,
        hourly_rate: Number.isFinite(rate) && rate > 0 ? rate : null,
        custom: {},
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return { inlineCreated: { slot: "project", id: data.id, name: data.name } };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const subscription_id = String(form.get("id") ?? "");
    if (subscription_id) {
      await apiFor(event).DELETE("/api/v1/subscriptions/{subscription_id}", {
        params: { path: { subscription_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
};
