import { fail } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { impexAction } from "$lib/core/impex/actions.server";
import { parseParty } from "$lib/core/party";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { readAutoInvoiceMode } from "$lib/modules/invoicing/types";
import { apiFor } from "$lib/core/session";
import { columnsForViewer, readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { DOMAIN_COLUMNS, DOMAINS_TABLE_ID } from "$lib/modules/domains/columns";
import { DOMAIN_FILTERS } from "$lib/modules/domains/filters";
import { readInvoiceable } from "$lib/modules/domains/normalize";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);

  // The bar and this load read the same source — `page.url` here, `event.url` there — so what
  // the controls show and what the API was asked for can never disagree (core/filters/types.ts).
  // The short URL keys are what a client card deep-links to and what a person pastes; the API's
  // own parameter names are an implementation detail this mapping keeps out of the address bar.
  const filters = readFilters(event.url, [...DOMAIN_FILTERS]);

  // The saved column layout comes from the layout load (docs/PERFORMANCE.md). The URL wins
  // over the saved sort so a sorted list stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, DOMAINS_TABLE_ID);
  const resolved = resolveColumns(columnsForViewer(DOMAIN_COLUMNS, event.locals.user), pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  const paging = resolvePaging(event.url, pref);

  // The list's filters, once: the page and its totals must be asked about the same set, or
  // the footer adds up rows the table above it does not show.
  const query = {
    q: filters.q,
    company_id: filters.company,
    status: filters.status,
    registrar_provider_id: filters.registrar,
    dns_provider_id: filters.dns,
    // Absent is every domain; "false" is a filter in its own right ("what am I *not*
    // billing?"), so this is a tri-state and never a plain boolean.
    invoiceable: filters.invoiceable ? filters.invoiceable === "true" : undefined,
  };

  // Only the URL-dependent reads; every picker and definition set comes from the section
  // layout, which does not rerun on search, filter or sort navigation (#290). The totals ride
  // beside the page rather than after it: the section headings and the footer are the API's
  // own aggregate over the whole filtered set, never a sum of the page (#37).
  const [domains, totals] = await Promise.all([
    api.GET("/api/v1/domains", {
      params: { query: { limit: paging.limit, offset: paging.offset, sort, ...query } },
    }),
    api.GET("/api/v1/domains/totals", { params: { query } }),
  ]);

  return {
    domains: domains.data?.items ?? [],
    total: domains.data?.total ?? 0,
    totals: totals.data ?? null,
    paging,
    agencyLabel: event.locals.theme?.brandName ?? "",
    filters,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "domain"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, DOMAINS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "domain"),
  bulkDelete: (event) => bulkDeleteAction(event, "domain"),

  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const company_id = String(form.get("company_id") ?? "");
    if (!name || !company_id) return fail(400, { error: "errors.required" });
    const email_enabled = form.get("email_enabled") !== null;

    const { error } = await apiFor(event).POST("/api/v1/domains", {
      body: {
        name,
        company_id,
        status: String(form.get("status") ?? "active") as never,
        redirect_url: String(form.get("redirect_url") ?? "").trim() || null,
        start_date: String(form.get("start_date") ?? "").trim() || undefined,
        // Left blank on purpose most of the time: the API resolves the register's expiry for
        // this name, else the first anniversary of the start date still ahead.
        next_invoice_date: String(form.get("next_invoice_date") ?? "").trim() || undefined,
        // "Already invoiced up to" (a migrated portfolio): blank says nothing.
        billed_until: String(form.get("billed_until") ?? "").trim() || null,
        price_override: String(form.get("price_override") ?? "").trim() || null,
        // Three-state (#298): "" is *follow the register*, not "no".
        invoiceable: readInvoiceable(form.get("invoiceable")),
        // "" is the inherit choice and must reach the API as an explicit null: NULL means
        // "follow the org", which is a third state rather than any of the levels.
        auto_invoice_mode: readAutoInvoiceMode(form.get("auto_invoice_mode")),
        registrar_provider_id: String(form.get("registrar_provider_id") ?? "") || null,
        dns_provider_id: String(form.get("dns_provider_id") ?? "") || null,
        registry_contact: parseParty(form.get("registry_contact")),
        email_enabled,
        email_provider_id: email_enabled
          ? String(form.get("email_provider_id") ?? "") || null
          : null,
        email_contact: email_enabled ? parseParty(form.get("email_contact")) : null,
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
    const domain_id = String(form.get("id") ?? "");
    if (domain_id) {
      await apiFor(event).DELETE("/api/v1/domains/{domain_id}", {
        params: { path: { domain_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,
};
