import { fail, redirect } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { impexAction } from "$lib/core/impex/actions.server";
import { parseParty } from "$lib/core/party";
import { can } from "$lib/core/permissions";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { WEBSITE_COLUMNS, WEBSITES_TABLE_ID } from "$lib/modules/websites/columns";
import { WEBSITE_FILTERS } from "$lib/modules/websites/filters";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  // The API enforces the permission too; redirect a member who lacks it (the nav item is
  // already hidden for them).
  if (!can(event.locals.user, "websites.website.read")) throw redirect(303, "/");
  const api = apiFor(event);

  // Read where the bar reads (core/filters/types.ts), then map onto the API's own names. `q`
  // and `company` both ask about the parent domain — the API crosses that bridge, not this.
  const filters = readFilters(event.url, [...WEBSITE_FILTERS]);

  // The saved column layout comes from the layout load (docs/PERFORMANCE.md). The URL wins
  // over the saved sort so a sorted list stays shareable and the back button works.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, WEBSITES_TABLE_ID);
  const resolved = resolveColumns(WEBSITE_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;

  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read; every picker and definition set comes from the section
  // layout, which does not rerun on a filter or sort click (#290).
  const websites = await api.GET("/api/v1/websites", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        sort,
        q: filters.q,
        company_id: filters.company,
        hosting_id: filters.hosting,
        // Tri-state: absent is every site, "false" is "what is *not* monitored".
        uptime_enabled: filters.uptime ? filters.uptime === "true" : undefined,
      },
    },
  });

  return {
    websites: websites.data?.items ?? [],
    total: websites.data?.total ?? 0,
    paging,
    filters,
    agencyLabel: event.locals.theme?.brandName ?? "",
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Import/export from this list's own toolbar (issue #77) — the shared wizard's three steps. */
  impex: (event) => impexAction(event, "website"),
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, WEBSITES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "website"),
  bulkDelete: (event) => bulkDeleteAction(event, "website"),

  save: async (event) => {
    const form = await event.request.formData();
    const website_id = String(form.get("website_id") ?? "");
    const body = {
      root: form.get("root") !== "www",
      technical_owner: parseParty(form.get("technical_owner")),
      hosting_id: String(form.get("hosting_id") ?? "") || null,
      uptime_enabled: form.get("uptime_enabled") !== null,
      custom: parseCustom(form.get("custom")),
    };
    if (website_id) {
      const { error } = await apiFor(event).PATCH("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
        body,
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    } else {
      const domain_id = String(form.get("domain_id") ?? "");
      if (!domain_id) return fail(400, { error: "errors.required" });
      const { error } = await apiFor(event).POST("/api/v1/websites", {
        body: { ...body, domain_id },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const website_id = String(form.get("id") ?? "");
    if (website_id) {
      await apiFor(event).DELETE("/api/v1/websites/{website_id}", {
        params: { path: { website_id } },
      });
    }
    return { deleted: true };
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,

  // Inline-create for the hosting picker (#115): the full HostingForm in a modal.
  createHosting: async (event) => {
    const form = await event.request.formData();
    const body = {
      name: String(form.get("name") ?? "").trim(),
      company_id: String(form.get("company_id") ?? "") || null,
      provider_id: String(form.get("provider_id") ?? "") || null,
      ip_address: String(form.get("ip_address") ?? "").trim() || null,
      contact: parseParty(form.get("contact")),
      custom: parseCustom(form.get("custom")),
    };
    if (!body.name) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/hosting", { body });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return { inlineCreated: { slot: "hosting_account", id: data.id } };
  },

  // Inline-create for the domain picker (#115): the full DomainForm in a modal. The new domain
  // is unclaimed, so the refreshed load re-lists it for the website's domain Combobox.
  createDomain: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const company_id = String(form.get("company_id") ?? "");
    if (!name || !company_id) return fail(400, { qcError: "errors.required" });
    const email_enabled = form.get("email_enabled") !== null;
    const { data, error } = await apiFor(event).POST("/api/v1/domains", {
      body: {
        name,
        company_id,
        status: String(form.get("status") ?? "active") as never,
        redirect_url: String(form.get("redirect_url") ?? "").trim() || null,
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
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return { inlineCreated: { slot: "domain", id: data.id } };
  },
};
