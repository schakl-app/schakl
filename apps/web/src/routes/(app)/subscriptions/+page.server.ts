import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import {
  SUBSCRIPTION_COLUMNS,
  SUBSCRIPTIONS_TABLE_ID,
} from "$lib/modules/subscriptions/columns";

import type { Actions, PageServerLoad } from "./$types";

const BULK_STATUSES = ["draft", "active", "paused", "cancelled"] as const;

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
    subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
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
  // The saved layout decides how the *server* sorts (#24); the URL wins so a sorted list
  // stays shareable. Filters live in URL params and the API applies them (#153).
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, SUBSCRIPTIONS_TABLE_ID);
  const resolved = resolveColumns(SUBSCRIPTION_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const typeFilter = event.url.searchParams.get("type") ?? undefined;
  const companyFilter = event.url.searchParams.get("company") || undefined;
  const statusFilter = event.url.searchParams.get("status") || undefined;

  const [subscriptions, summary, types, templates, companies, projects, definitions, companyDefinitions] =
    await Promise.all([
    api.GET("/api/v1/subscriptions", {
      params: {
        query: {
          limit: 200,
          offset: 0,
          sort,
          subscription_type_id: typeFilter,
          company_id: companyFilter,
          status: statusFilter as "active" | undefined,
        },
      },
    }),
    api.GET("/api/v1/subscriptions/summary"),
    api.GET("/api/v1/subscriptions/types"),
    api.GET("/api/v1/subscriptions/templates"),
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
    types: types.data ?? [],
    templates: templates.data ?? [],
    typeFilter: typeFilter ?? "",
    companyFilter: companyFilter ?? "",
    statusFilter: statusFilter ?? "",
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    canManageTypes: can(event.locals.user, "subscriptions.type.manage"),
    canManageTemplates: can(event.locals.user, "subscriptions.template.manage"),
    companies: lookupItems(companies, "companies").map((c) => ({ id: c.id, name: c.name })),
    projects: lookupItems(projects, "projects").map((p) => ({ id: p.id, name: p.name })),
    definitions: definitions.data ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, SUBSCRIPTIONS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** Bulk status change (#153): a per-id fan-out — the single PATCH is the validation path,
   *  so a bulk action can never do what a row edit could not. */
  bulkStatus: async (event) => {
    const form = await event.request.formData();
    const status = String(form.get("status") ?? "");
    const ids = form
      .getAll("ids")
      .flatMap((value) => String(value).split(","))
      .map((value) => value.trim())
      .filter(Boolean);
    if (!ids.length || !BULK_STATUSES.includes(status as (typeof BULK_STATUSES)[number])) {
      return fail(400, { error: "errors.required" });
    }
    const api = apiFor(event);
    for (const subscription_id of ids) {
      const { error } = await api.PATCH("/api/v1/subscriptions/{subscription_id}", {
        params: { path: { subscription_id } },
        body: { status: status as "active" },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { bulkUpdated: ids.length };
  },

  bulkDelete: async (event) => {
    const form = await event.request.formData();
    const ids = form
      .getAll("ids")
      .flatMap((value) => String(value).split(","))
      .map((value) => value.trim())
      .filter(Boolean);
    if (!ids.length) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    for (const subscription_id of ids) {
      await api.DELETE("/api/v1/subscriptions/{subscription_id}", {
        params: { path: { subscription_id } },
      });
    }
    return { bulkDeleted: ids.length };
  },

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

  /** Inline type create from the form's picker (docs/UX.md — per-picker definition of done).
   *  The full type dialog minus the spawn list, which lives in Instellingen → Abonnementen. */
  createType: async (event) => {
    const form = await event.request.formData();
    const key = String(form.get("key") ?? "").trim();
    const label_i18n = {
      nl: String(form.get("label_nl") ?? "").trim(),
      en: String(form.get("label_en") ?? "").trim(),
    };
    if (!key || !label_i18n.nl || !label_i18n.en) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/subscriptions/types", {
      body: { key, label_i18n, position: 0, active: true, task_template_ids: [] },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return { inlineCreated: { slot: "subscription_type", id: data.id, name: label_i18n.nl } };
  },

  /** "Opslaan als sjabloon" on a row (UX rule 5: templates are creatable from where you
   *  work). The row posts its own preset values; managing them lives under Instellingen. */
  saveTemplate: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const amount = String(form.get("amount") ?? "").trim();
    const included = String(form.get("included_hours") ?? "").trim();
    const notice = String(form.get("notice_period_days") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/subscriptions/templates", {
      body: {
        name,
        subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
        interval: String(form.get("interval") ?? "monthly") as "monthly",
        interval_count: Number(form.get("interval_count") ?? 1) || 1,
        amount: amount || null,
        included_hours: included || null,
        notice_period_days: notice ? Number(notice) : null,
        notes: String(form.get("notes") ?? "").trim() || null,
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { templateSaved: true };
  },

  createCompany: createCompanyAction,
};
