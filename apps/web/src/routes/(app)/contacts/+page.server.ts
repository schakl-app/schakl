import { fail } from "@sveltejs/kit";

import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { impexAction } from "$lib/core/impex/actions.server";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { CONTACT_COLUMNS, CONTACTS_TABLE_ID } from "$lib/modules/contacts/columns";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const q = event.url.searchParams.get("q") || undefined;

  // The saved layout decides the sort the *server* applies — a paginated list sorted in the
  // browser sorts the wrong set. It comes from the layout load, which does not rerun on filter
  // navigation (docs/PERFORMANCE.md). The URL wins so a sorted list stays shareable.
  const { prefs } = await event.parent();
  const pref = readTablePref(prefs, CONTACTS_TABLE_ID);
  const resolved = resolveColumns(CONTACT_COLUMNS, pref);
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? undefined;
  const contact_type_id = event.url.searchParams.get("type") || undefined;
  // Client filter (#154) — applied by the API; the URL keeps it shareable.
  const company_id = event.url.searchParams.get("company") || undefined;

  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read. The definitions, the client picker and the type vocabulary
  // come from the section layout, which does not rerun on filter navigation (#290).
  const contacts = await api.GET("/api/v1/contacts", {
    params: {
      query: {
        limit: paging.limit,
        offset: paging.offset,
        q,
        sort,
        contact_type_id,
        company_id,
      },
    },
  });
  return {
    contacts: contacts.data?.items ?? [],
    total: contacts.data?.total ?? 0,
    paging,
    typeFilter: contact_type_id ?? "",
    companyFilter: company_id ?? "",
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    locale: event.locals.locale,
  };
};

/** The create form serialises the picked company IDs into one hidden JSON field (#80). */
function parseCompanyIds(raw: FormDataEntryValue | null): string[] {
  try {
    const value = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

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
    await saveTablePref(event, CONTACTS_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** CSV import (issue #77): dry-run preview by default, all-or-nothing commit on demand. */
  impex: (event) => impexAction(event, "contact"),

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "contact"),
  bulkDelete: (event) => bulkDeleteAction(event, "contact"),

  /** Inline company create from the "connected companies" picker (#115). */
  createCompany: createCompanyAction,

  create: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });

    const company_ids = parseCompanyIds(form.get("company_ids"));
    const { error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        // The API links each and promotes the first to the company's primary contact.
        company_ids: company_ids.length ? company_ids : undefined,
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
      await apiFor(event).DELETE("/api/v1/contacts/{contact_id}", {
        params: { path: { contact_id: id } },
      });
    }
    return { deleted: true };
  },
};
