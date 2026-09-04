import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { impexAction } from "$lib/core/impex/actions.server";
import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { SUBSCRIPTION_COLUMNS, SUBSCRIPTIONS_TABLE_ID } from "$lib/modules/subscriptions/columns";
import { SUBSCRIPTION_FILTERS } from "$lib/modules/subscriptions/filters";
import {
  createSubscription,
  createSubscriptionProject,
  createSubscriptionType,
  subscriptionBody,
} from "$lib/modules/subscriptions/actions.server";
import { manageActions } from "$lib/modules/subscriptions/manage.server";
import { priceIncreaseActions } from "$lib/modules/subscriptions/priceincrease.server";

import type { Actions, PageServerLoad } from "./$types";

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

  // The create and the two picker quick-creates live in `actions.server.ts` so a client's page
  // can mount the same form in a dialog of its own; this page mounts them under its short names.
  create: createSubscription,

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

  createProject: createSubscriptionProject,

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

  createType: createSubscriptionType,

  // Price increase (#231): preview + apply over an all / type / subscription / template
  // scope, shared with the standard-subscriptions tab (priceincrease.server.ts).
  ...priceIncreaseActions,

  createCompany: createCompanyAction,

  // Types + templates beheer, shared with Instellingen → Abonnementen (manage.server.ts).
  // Its `saveTemplate` also serves the row's "Opslaan als sjabloon" hidden form.
  ...manageActions,
};
