import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { impexAction } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { readAutoInvoiceMode } from "$lib/modules/invoicing/types";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { SUBSCRIPTION_COLUMNS, SUBSCRIPTIONS_TABLE_ID } from "$lib/modules/subscriptions/columns";
import { SUBSCRIPTION_FILTERS } from "$lib/modules/subscriptions/filters";
import { manageActions, parseLabelI18n } from "$lib/modules/subscriptions/manage.server";
import { priceIncreaseActions } from "$lib/modules/subscriptions/priceincrease.server";

import type { Actions, PageServerLoad } from "./$types";

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
    // "" is the inherit choice, and it must reach the API as an explicit null: the column's
    // third state is "follow the org", which is not the same as any level.
    auto_invoice_mode: readAutoInvoiceMode(form.get("auto_invoice_mode")),
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
  // The MRR strip and the preset library are the module's own surfaces, at `:any` (#266's
  // rule): a client holding `:own` reads their agreements and neither of those, so the two
  // reads are skipped rather than made and refused.
  const canReadAny = can(event.locals.user, "subscriptions.subscription.read", "any");
  // The saved layout decides how the *server* sorts (#24); the URL wins so a sorted list
  // stays shareable. Filters live in URL params and the API applies them (#153).
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, SUBSCRIPTIONS_TABLE_ID);
  const resolved = resolveColumns(SUBSCRIPTION_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  // The bar and this load read the same source, so what the controls show and what the API was
  // asked for can never disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...SUBSCRIPTION_FILTERS]);
  const typeFilter = filters.type;
  const companyFilter = filters.company;
  const statusFilter = filters.status;
  const paging = resolvePaging(event.url, pref);

  // The client/project pickers and the two custom-field sets come from the section layout, which
  // does not rerun on filter or sort navigation (#290).
  const [subscriptions, summary, types, templates, invoicingSettings] = await Promise.all([
    api.GET("/api/v1/subscriptions", {
      params: {
        query: {
          limit: paging.limit,
          offset: paging.offset,
          sort,
          subscription_type_id: typeFilter,
          company_id: companyFilter,
          status: statusFilter as "active" | undefined,
          q: filters.q,
        },
      },
    }),
    canReadAny ? api.GET("/api/v1/subscriptions/summary") : Promise.resolve({ data: undefined }),
    // Managers get inactive types too, so a row referencing one still shows its label.
    api.GET("/api/v1/subscriptions/types", {
      params: { query: { include_inactive: canManageTypes || canManageTemplates } },
    }),
    canReadAny ? api.GET("/api/v1/subscriptions/templates") : Promise.resolve({ data: undefined }),
    // Only to name the inherited level in the form's "follow the organisation" hint. A
    // caller who cannot read invoicing settings (or an instance without the module) simply
    // gets the seeded default in the hint, never an error.
    api.GET("/api/v1/invoicing/settings"),
  ]);

  return {
    subscriptions: subscriptions.data?.items ?? [],
    total: subscriptions.data?.total ?? 0,
    paging,
    summary: summary.data ?? null,
    types: types.data ?? [],
    templates: templates.data ?? [],
    invoicingSettings: invoicingSettings.data ?? null,
    filters,
    typeFilter: typeFilter ?? "",
    companyFilter: companyFilter ?? "",
    statusFilter: statusFilter ?? "",
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    canManageTypes,
    canManageTemplates,
    canWrite: can(event.locals.user, "subscriptions.subscription.write"),
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "subscription"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, SUBSCRIPTIONS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /**
   * The ✎ menu's two actions, shared by every list that has one.
   *
   * These replace the hand-written pair this page used to own (#153): a per-id loop through the
   * single-record endpoint, which stopped at the first refusal and reported only a count — so a
   * batch was however far it got before it gave up, with no way to say which rows were skipped
   * or why. The API's bulk endpoint does the whole selection and reports the leftovers.
   */
  bulkUpdate: (event) => bulkUpdateAction(event, "subscription"),
  bulkDelete: (event) => bulkDeleteAction(event, "subscription"),

  create: async (event) => {
    const form = await event.request.formData();
    const body = subscriptionBody(form);
    const company_id = String(form.get("company_id") ?? "");
    if (!body.name || !company_id || !body.start_date || body.amount === undefined) {
      return fail(400, { error: "errors.required" });
    }
    // Only create carries it: it records which preset the form was prefilled from, so a
    // later rename of that standard subscription reaches this agreement (an edit never
    // re-links, and renaming the agreement itself is how it stops following).
    const subscription_template_id =
      String(form.get("subscription_template_id") ?? "").trim() || null;
    const { error } = await apiFor(event).POST("/api/v1/subscriptions", {
      body: { ...body, company_id, amount: body.amount, subscription_template_id } as never,
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
    // A project belongs to a client (`ProjectCreate`): named here so the dialog says
    // which field, instead of relaying a bare validation envelope.
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { qcError: "errors.projects_company_required" });
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        // Made for an agreement, so it starts non-billable (#284): the retainer already pays
        // for this work. Saving the agreement links it and would clear the flag anyway — this
        // is so the project reads right the moment it exists, not one save later.
        billable_default: false,
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

  // Price increase (#231): preview + apply over an all / type / subscription / template
  // scope, shared with the standard-subscriptions tab (priceincrease.server.ts).
  ...priceIncreaseActions,

  createCompany: createCompanyAction,

  // Types + templates beheer, shared with Instellingen → Abonnementen (manage.server.ts).
  // Its `saveTemplate` also serves the row's "Opslaan als sjabloon" hidden form.
  ...manageActions,
};
