import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { impexAction } from "$lib/core/impex/actions.server";
import { parseParty } from "$lib/core/party";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { readAutoInvoiceMode } from "$lib/modules/invoicing/types";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { DOMAIN_COLUMNS, DOMAINS_TABLE_ID } from "$lib/modules/domains/columns";
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
  const q = event.url.searchParams.get("q") || undefined;

  // The saved column layout comes from the layout load (docs/PERFORMANCE.md). The URL wins
  // over the saved sort so a sorted list stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, DOMAINS_TABLE_ID);
  const resolved = resolveColumns(DOMAIN_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read; every picker and definition set comes from the section
  // layout, which does not rerun on search or sort navigation (#290).
  const domains = await api.GET("/api/v1/domains", {
    params: { query: { limit: paging.limit, offset: paging.offset, q, sort } },
  });

  return {
    domains: domains.data?.items ?? [],
    total: domains.data?.total ?? 0,
    paging,
    agencyLabel: event.locals.theme?.brandName ?? "",
    q: q ?? "",
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
