import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, lookupItems } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { SUBSCRIPTION_COLUMNS, SUBSCRIPTIONS_TABLE_ID } from "$lib/modules/subscriptions/columns";
import { manageActions, parseLabelI18n } from "$lib/modules/subscriptions/manage.server";

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
function parseLinks(
  raw: FormDataEntryValue | null,
): { entity_type: "project"; entity_id: string }[] {
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

const PRICE_MODES = ["percent", "amount", "set"] as const;

/** The bulk price-increase fields, shared by preview and apply. `null` = invalid. */
function priceIncreaseBody(form: FormData) {
  const mode = String(form.get("mode") ?? "");
  const value = String(form.get("value") ?? "").trim();
  const valid_from = String(form.get("valid_from") ?? "").trim();
  if (!PRICE_MODES.includes(mode as (typeof PRICE_MODES)[number])) return null;
  if (!value || Number.isNaN(Number(value)) || !valid_from) return null;
  return {
    mode: mode as (typeof PRICE_MODES)[number],
    value,
    valid_from,
    subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
    include_templates: form.get("include_templates") !== null,
  };
}

/** The recurring-agreement fields both actions share (#30). */
function subscriptionBody(form: FormData) {
  const amount = String(form.get("amount") ?? "").trim();
  return {
    name: String(form.get("name") ?? "").trim(),
    subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
    status: String(form.get("status") ?? "active") as "active",
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
  const canManageTypes = can(event.locals.user, "subscriptions.type.manage");
  const canManageTemplates = can(event.locals.user, "subscriptions.template.manage");
  // The saved layout decides how the *server* sorts (#24); the URL wins so a sorted list
  // stays shareable. Filters live in URL params and the API applies them (#153).
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, SUBSCRIPTIONS_TABLE_ID);
  const resolved = resolveColumns(SUBSCRIPTION_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const typeFilter = event.url.searchParams.get("type") ?? undefined;
  const companyFilter = event.url.searchParams.get("company") || undefined;
  const statusFilter = event.url.searchParams.get("status") || undefined;

  const [
    subscriptions,
    summary,
    types,
    templates,
    companies,
    projects,
    definitions,
    companyDefinitions,
  ] = await Promise.all([
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
    // Managers get inactive types too, so a row referencing one still shows its label.
    api.GET("/api/v1/subscriptions/types", {
      params: { query: { include_inactive: canManageTypes || canManageTemplates } },
    }),
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
    canManageTypes,
    canManageTemplates,
    canWrite: can(event.locals.user, "subscriptions.subscription.write"),
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
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id: String(form.get("company_id") ?? "").trim() || null,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: true,
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
   *  The full type dialog minus the spawn list. One label language is enough (docs/UX.md):
   *  a missing locale falls back at render time. */
  createType: async (event) => {
    const form = await event.request.formData();
    const label_i18n = parseLabelI18n(form);
    if (Object.keys(label_i18n).length === 0) {
      return fail(400, { qcError: "errors.required" });
    }
    // The tenant only types the label; the immutable key is derived from it (#234).
    const key = slugify(label_i18n.nl || label_i18n.en || "");
    if (!key) return fail(400, { qcError: "errors.label_no_key" });
    const { data, error, response } = await apiFor(event).POST("/api/v1/subscriptions/types", {
      body: { key, label_i18n, position: 0, active: true, task_template_ids: [] },
    });
    if (error || !data) return fail(400, { qcError: createErrorKey(error, response) });
    const name = label_i18n.nl || label_i18n.en || key;
    return { inlineCreated: { slot: "subscription_type", id: data.id, name } };
  },

  /** Bulk price increase: preview shows every in-scope agreement with its would-be amount;
   *  apply appends the dated price rows (and optionally bumps template defaults). */
  previewPriceIncrease: async (event) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/subscriptions/price-increase/preview",
      { body: body as never },
    );
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    return { pricePreview: data };
  },

  applyPriceIncrease: async (event) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/subscriptions/price-increase", {
      body: body as never,
    });
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    return { priceApplied: data.items.length };
  },

  createCompany: createCompanyAction,

  // Types + templates beheer, shared with Instellingen → Abonnementen (manage.server.ts).
  // Its `saveTemplate` also serves the row's "Opslaan als sjabloon" hidden form.
  ...manageActions,
};
