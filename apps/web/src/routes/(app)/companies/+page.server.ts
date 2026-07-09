import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

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
  const q = event.url.searchParams.get("q") || undefined;
  const api = apiFor(event);

  // The create form's lookups are streamed, not awaited: the list paints on the companies call
  // alone, and the (hidden) form fills in behind it. See docs/PERFORMANCE.md.
  const createForm = Promise.all([
    api.GET("/api/v1/members/lookup"),
    api.GET("/api/v1/contacts", { params: { query: { limit: 200, offset: 0 } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "company" } } }),
    api.GET("/api/v1/custom-fields/definitions", { params: { query: { entity_type: "contact" } } }),
  ])
    .then(([members, contacts, definitions, contactDefinitions]) => ({
      members: members.data ?? [],
      contacts: contacts.data?.items ?? [],
      definitions: definitions.data ?? [],
      contactDefinitions: contactDefinitions.data ?? [],
    }))
    .catch(() => ({ members: [], contacts: [], definitions: [], contactDefinitions: [] }));

  const companiesRes = await api.GET("/api/v1/companies", {
    params: { query: { limit: 200, offset: 0, q } },
  });

  return {
    companies: companiesRes.data?.items ?? [],
    total: companiesRes.data?.total ?? 0,
    createForm,
    statusFilter: event.url.searchParams.get("status") ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
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
        website: website || null,
        notes: notes || null,
        status: String(form.get("status") ?? "active") as "active",
        responsible_user_id: String(form.get("responsible_user_id") ?? "") || null,
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
