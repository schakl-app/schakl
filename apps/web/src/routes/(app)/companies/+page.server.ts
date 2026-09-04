import { fail } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { bulkDeleteAction, bulkUpdateAction } from "$lib/core/bulk/actions.server";
import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { impexAction } from "$lib/core/impex/actions.server";
import { apiFor } from "$lib/core/session";
import { readTablePref, resolveColumns } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { COMPANIES_TABLE_ID, HOURS_COLUMN, companyColumns } from "$lib/modules/companies/columns";
import { COMPANY_FILTERS } from "$lib/modules/companies/filters";
import { COMPANY_STATUS_ALL, COMPANY_WORKING_SET } from "$lib/modules/companies/status";

import type { Actions, PageServerLoad } from "./$types";

/** One picked contact person on the create form: an existing contact, or a draft to create. */
interface ContactSelection {
  contact_id?: string;
  draft?: {
    first_name?: string;
    last_name?: string;
    email?: string;
    phone?: string;
    job_title?: string;
    custom?: Record<string, unknown>;
  };
  is_primary?: boolean;
}

function parseJson<T>(raw: FormDataEntryValue | null, fallback: T): T {
  try {
    return JSON.parse(String(raw ?? "")) as T;
  } catch {
    return fallback;
  }
}

function parseContacts(raw: FormDataEntryValue | null): ContactSelection[] {
  const parsed = parseJson<unknown>(raw, []);
  return Array.isArray(parsed) ? (parsed as ContactSelection[]) : [];
}

export const load: PageServerLoad = async (event) => {
  // The bar and this load read the same keys from the same place, so what the controls show and
  // what the API was asked for cannot disagree (core/filters/types.ts).
  const filters = readFilters(event.url, [...COMPANY_FILTERS]);
  const q = filters.q;
  // The layout question (#373): a client reads their own companies, so the agency's lifecycle
  // pills, the "my clients" narrowing and the agency-side columns are not part of this screen.
  const isPortal = event.locals.user?.isPortal ?? false;
  // "My clients" is filtered by the API (any assignee, not just the primary), never in the page.
  // A client holds no assignments, so for a portal login the token is dropped rather than sent:
  // the control is not drawn for them, and a pasted staff link must not open an empty list.
  const mine = filters.mine === "1" && !isPortal;
  // So is the status pill. It used to narrow `data.companies` in the browser, which was survivable
  // only while the page *was* the list; against a paged list it would filter the fifty rows you
  // happen to hold and report a total counted over all of them. The export already sent `status`
  // to the API, so the screen and its spreadsheet now agree by construction.
  //
  // Klanten opens on the working book of business — every status except archived (#329) — so the
  // URL token and the wire value are no longer the same string. Absent means the working set;
  // `all` is how "everything, archive included" says so in a URL you can link to; anything else
  // is that one status. Only the resolution lives here: the *set* is in `status.ts`, beside the
  // pills, so the screen and the export cannot end up with two ideas of what is archived.
  // A client's list is their own companies and nothing about their lifecycle with the agency
  // is theirs to filter on: no pills, and no narrowing — the horizon already decides the set.
  const statusFilter = isPortal ? "" : (filters.status ?? "");
  const status = isPortal
    ? undefined
    : statusFilter === COMPANY_STATUS_ALL
      ? undefined
      : statusFilter || COMPANY_WORKING_SET;
  const api = apiFor(event);

  // The saved column layout comes from the layout load, which does not rerun on filter or sort
  // navigation (docs/PERFORMANCE.md). Two things depend on it before a single row is fetched:
  // This one *must* precede the fetch below — the saved sort and the hidden-column decision are
  // query parameters of it — and `event.parent()` is memoised, so the layout's own calls are
  // already in flight by the time this resolves.
  const parent = await event.parent();
  const pref = readTablePref(parent.prefs, COMPANIES_TABLE_ID);
  // A client's register has no agency-side columns (`audience: "staff"` on the column), and
  // resolving against the narrowed list is what keeps a saved layout naming `hours` from
  // asking the API for a roll-up it would blank anyway.
  const resolved = resolveColumns(companyColumns(isPortal), pref);

  //   1. the sort, which the *server* applies — sorting one page of a longer list in the browser
  //      sorts the wrong set. The URL wins over the saved default so a sorted list stays
  //      shareable and the back button works, and A–Z is what is left when neither says
  //      anything: a client register reads alphabetically, and someone who deliberately sorted
  //      on klantnummer keeps that (#329). Stated here as well as in the API's own default so
  //      the column header shows the arrow on the column the rows are actually ordered by.
  const sort = event.url.searchParams.get("sort") ?? resolved.sort ?? "name";
  //   2. whether to pay for the budget roll-up at all. Hidden column, no aggregate (#24).
  const hours = resolved.columns.some((column) => column.key === HOURS_COLUMN);
  //   3. the page size, whose saved default the URL likewise overrides (`paging.ts`).
  const paging = resolvePaging(event.url, pref);

  // Only the URL-dependent read happens here. The custom-field definitions the table renders
  // its columns from, and the member names the assignee column needs, come from the section
  // layout — which does not rerun on filter or sort navigation (#290).
  const companiesRes = await api.GET("/api/v1/companies", {
    params: {
      query: { limit: paging.limit, offset: paging.offset, q, mine, status, sort, hours },
    },
  });

  const definitions = parent.definitions;
  const members = parent.members;

  // The create form's remaining lookups still stream in behind the list.
  const createForm = Promise.all([
    api.GET("/api/v1/contacts", {
      params: { query: { limit: 200, offset: 0, sort: "first_name" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "contact" } } }),
  ])
    .then(([contacts, contactDefinitions]) => ({
      members,
      contacts: contacts.data?.items ?? [],
      definitions,
      contactDefinitions: contactDefinitions.data ?? [],
    }))
    .catch(() => ({ members, contacts: [], definitions, contactDefinitions: [] }));

  return {
    companies: companiesRes.data?.items ?? [],
    total: companiesRes.data?.total ?? 0,
    paging,
    createForm,
    definitions,
    members,
    table: { pref, sort: sort ?? null, widths: resolved.widths },
    // Two values, on purpose: the pills highlight on the *token* the URL carries, and the export
    // sends the *resolved* filter — the whole point of `ImpexBar`'s `filters` is that the file
    // holds what the screen holds, and with a default that narrows, passing the token would let
    // the archived clients quietly back into the spreadsheet.
    statusFilter,
    statusQuery: status ?? "",
    mine,
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  /** Persist this user's column layout. Personal, in-view — never org settings (docs/UX.md §6). */
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, COMPANIES_TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },

  /** CSV import (issue #77): dry-run preview by default, all-or-nothing commit on demand. */
  impex: (event) => impexAction(event, "company"),

  /** The ✎ menu's two actions, shared by every list that has one. */
  bulkUpdate: (event) => bulkUpdateAction(event, "company"),
  bulkDelete: (event) => bulkDeleteAction(event, "company"),

  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    const website = String(form.get("website") ?? "").trim();
    const notes = String(form.get("notes") ?? "").trim();
    const { data: company, error } = await api.POST("/api/v1/companies", {
      body: {
        name,
        // Blank means "the label is also the legal name" — never a guess at one.
        legal_name: String(form.get("legal_name") ?? "").trim() || null,
        // Blank means "allocate one" on create; the API decides per the org's settings.
        client_number: String(form.get("client_number") ?? "").trim() || null,
        website: website || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        invoice_email: String(form.get("invoice_email") ?? "").trim() || null,
        vat_number: String(form.get("vat_number") ?? "").trim() || null,
        coc_number: String(form.get("coc_number") ?? "").trim() || null,
        address_line1: String(form.get("address_line1") ?? "").trim() || null,
        house_number: String(form.get("house_number") ?? "").trim() || null,
        address_line2: String(form.get("address_line2") ?? "").trim() || null,
        postal_code: String(form.get("postal_code") ?? "").trim() || null,
        city: String(form.get("city") ?? "").trim() || null,
        country:
          String(form.get("country") ?? "")
            .trim()
            .toUpperCase() || null,
        notes: notes || null,
        status: String(form.get("status") ?? "active") as "active",
        assignees: parseAssignees(form.get("assignees")),
        custom: parseJson<Record<string, unknown>>(form.get("custom"), {}),
      },
    });
    if (error || !company) return fail(400, { error: apiErrorKey(error).key });

    const selections = parseContacts(form.get("contacts"));
    if (selections.length === 0) return { created: true };

    // Nothing below runs in one transaction — the web only ever talks to the API (Golden Rule 6) —
    // so every step records how to undo itself. Without that, a rejected contact would leave a
    // contactless company behind and the obvious retry would create a second one.
    const undo: Array<() => Promise<unknown>> = [
      () =>
        api.DELETE("/api/v1/companies/{company_id}", {
          params: { path: { company_id: company.id } },
        }),
    ];
    const rollback = async (key: string) => {
      for (const step of undo) await step();
      return fail(400, { error: key });
    };

    // Turn the drafts into real contacts (full create, custom fields and all). Unlinked for now:
    // the links are made below, in one place, so the primary is set the same way either way.
    const created = await Promise.all(
      selections.map(async (selection) => {
        const draft = selection.draft;
        if (!draft?.first_name?.trim()) return { id: null, error: null };
        const { data, error: draftError } = await api.POST("/api/v1/contacts", {
          body: {
            first_name: draft.first_name.trim(),
            last_name: draft.last_name?.trim() || null,
            email: draft.email?.trim() || null,
            phone: draft.phone?.trim() || null,
            job_title: draft.job_title?.trim() || null,
            company_ids: [],
            custom: draft.custom ?? {},
          },
        });
        return { id: data?.id ?? null, error: draftError ?? null };
      }),
    );
    for (const { id } of created) {
      if (id) {
        undo.push(() =>
          api.DELETE("/api/v1/contacts/{contact_id}", { params: { path: { contact_id: id } } }),
        );
      }
    }
    const draftError = created.find((c) => c.error)?.error;
    if (draftError) return rollback(apiErrorKey(draftError).key);

    const attach = selections
      .map((selection, i) => ({
        contact_id: selection.contact_id ?? created[i].id,
        is_primary: Boolean(selection.is_primary),
      }))
      .filter((c): c is { contact_id: string; is_primary: boolean } => Boolean(c.contact_id));

    // One at a time, not in parallel: the API reads `is_primary: false` as "decide for me" and
    // promotes the contact if the company has no primary yet, so concurrent links would race to
    // become primary and trip the one-primary-per-company unique index.
    for (const { contact_id } of attach) {
      const { error: linkError } = await api.POST("/api/v1/contacts/{contact_id}/links", {
        params: { path: { contact_id } },
        body: { company_id: company.id, is_primary: false },
      });
      if (linkError) return rollback(apiErrorKey(linkError).key);
    }
    // That auto-promote made the first attached contact primary; naming the chosen one last is
    // what makes the user's star stick.
    const primary = attach.find((c) => c.is_primary) ?? attach[0];
    if (primary) {
      const { error: primaryError } = await api.PATCH(
        "/api/v1/contacts/{contact_id}/links/{company_id}",
        {
          params: { path: { contact_id: primary.contact_id, company_id: company.id } },
          body: { is_primary: true },
        },
      );
      if (primaryError) return rollback(apiErrorKey(primaryError).key);
    }

    return { created: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      await apiFor(event).DELETE("/api/v1/companies/{company_id}", {
        params: { path: { company_id: id } },
      });
    }
    return { deleted: true };
  },
};
