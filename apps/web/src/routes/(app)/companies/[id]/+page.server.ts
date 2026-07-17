import { error, fail, redirect } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { apiBaseUrl } from "$lib/core/api/client";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import { driveActions } from "$lib/modules/google/drive-actions.server";
import { marketingActions } from "$lib/modules/marketing/actions.server";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** One picked contact person on the client form: an existing contact, or a draft to create. */
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

/** `undefined` when the field wasn't rendered — "I didn't say", not "no contacts". */
function parseContacts(raw: FormDataEntryValue | null): ContactSelection[] | undefined {
  if (raw == null) return undefined;
  try {
    const parsed: unknown = JSON.parse(String(raw));
    return Array.isArray(parsed) ? (parsed as ContactSelection[]) : undefined;
  } catch {
    return undefined;
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const company_id = event.params.id;

  const { data: company } = await api.GET("/api/v1/companies/{company_id}", {
    params: { path: { company_id } },
  });
  if (!company) throw error(404, { code: "not_found", message: "errors.not_found" });

  // The edit modal shows every editable field, contact persons included, so it needs the org's
  // contacts to pick from. One call covers both jobs: each `ContactRead` carries the companies it
  // is linked to with the per-company `is_primary`, so the client's current contacts are a filter
  // over this list rather than a second request (docs/PERFORMANCE.md).
  const [panels, definitions, templates, members, contacts, contactDefinitions] = await Promise.all(
    [
      api.GET("/api/v1/companies/{company_id}/panels", { params: { path: { company_id } } }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "company" } },
      }),
      api.GET("/api/v1/tasks/templates"),
      api.GET("/api/v1/members/lookup"),
      api.GET("/api/v1/contacts", { params: { query: { limit: 200, offset: 0 } } }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "contact" } },
      }),
    ],
  );

  return {
    company,
    panels: panels.data ?? [],
    definitions: definitions.data ?? [],
    templates: templates.data ?? [],
    members: members.data ?? [],
    contacts: contacts.data?.items ?? [],
    contactDefinitions: contactDefinitions.data ?? [],
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    const company_id = event.params.id;
    const { error: apiError } = await api.PATCH("/api/v1/companies/{company_id}", {
      params: { path: { company_id } },
      body: {
        name,
        website: String(form.get("website") ?? "").trim() || null,
        invoice_email: String(form.get("invoice_email") ?? "").trim() || null,
        vat_number: String(form.get("vat_number") ?? "").trim() || null,
        coc_number: String(form.get("coc_number") ?? "").trim() || null,
        address_line1: String(form.get("address_line1") ?? "").trim() || null,
        address_line2: String(form.get("address_line2") ?? "").trim() || null,
        postal_code: String(form.get("postal_code") ?? "").trim() || null,
        city: String(form.get("city") ?? "").trim() || null,
        country:
          String(form.get("country") ?? "")
            .trim()
            .toUpperCase() || null,
        notes: String(form.get("notes") ?? "").trim() || null,
        status: String(form.get("status") ?? "active") as "active",
        assignees: parseAssignees(form.get("assignees")),
        custom: parseCustom(form.get("custom")),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });

    // Per-client logo (#196): a chosen file replaces it; the checkbox removes it. Multipart
    // goes through a plain fetch — the typed client has no multipart serializer.
    const logoFile = form.get("logo_file");
    if (logoFile instanceof File && logoFile.size > 0) {
      const body = new FormData();
      body.append("file", logoFile, logoFile.name);
      const res = await event.fetch(
        `${apiBaseUrl()}/api/v1/companies/${company_id}/logo`,
        {
          method: "POST",
          headers: {
            cookie: event.request.headers.get("cookie") ?? "",
            "x-forwarded-host": event.request.headers.get("host") ?? "",
          },
          body,
        },
      );
      if (!res.ok) {
        return fail(400, {
          error: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type",
        });
      }
    } else if (form.get("logo_remove")) {
      await api.DELETE("/api/v1/companies/{company_id}/logo", {
        params: { path: { company_id } },
      });
    }

    const selections = parseContacts(form.get("contacts"));
    if (selections === undefined) return { updated: true };

    // Turn drafts into real contacts, unlinked — the links are all made below, in one place, so
    // the primary is decided the same way whichever route a contact arrived by.
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
    const draftError = created.find((c) => c.error)?.error;
    if (draftError) return fail(400, { error: apiErrorKey(draftError).key });

    const desired = selections
      .map((selection, i) => ({
        contact_id: selection.contact_id ?? created[i].id,
        is_primary: Boolean(selection.is_primary),
      }))
      .filter((c): c is { contact_id: string; is_primary: boolean } => Boolean(c.contact_id));

    // Reconcile against what the client already has rather than trusting the browser's idea of it:
    // the panel on this page can attach a contact between the modal opening and its save.
    const { data: linked } = await api.GET("/api/v1/contacts", {
      params: { query: { company_id, limit: 200, offset: 0 } },
    });
    const current = (linked?.items ?? []).map((c) => c.id);
    const wanted = new Set(desired.map((c) => c.contact_id));

    for (const contact_id of current.filter((id) => !wanted.has(id))) {
      await api.DELETE("/api/v1/contacts/{contact_id}/links/{company_id}", {
        params: { path: { contact_id, company_id } },
      });
    }
    // One at a time, not in parallel: the API reads `is_primary: false` as "decide for me" and
    // promotes the contact if the company has no primary yet, so concurrent links would race to
    // become primary and trip the one-primary-per-company unique index.
    for (const { contact_id } of desired.filter((c) => !current.includes(c.contact_id))) {
      const { error: linkError } = await api.POST("/api/v1/contacts/{contact_id}/links", {
        params: { path: { contact_id } },
        body: { company_id, is_primary: false },
      });
      if (linkError) return fail(400, { error: apiErrorKey(linkError).key });
    }
    // Naming the chosen one last is what makes the user's star stick, over any auto-promote above.
    const primary = desired.find((c) => c.is_primary) ?? desired[0];
    if (primary) {
      const { error: primaryError } = await api.PATCH(
        "/api/v1/contacts/{contact_id}/links/{company_id}",
        {
          params: { path: { contact_id: primary.contact_id, company_id } },
          body: { is_primary: true },
        },
      );
      if (primaryError) return fail(400, { error: apiErrorKey(primaryError).key });
    }

    return { updated: true };
  },

  // Create a new contact person and attach it to this client in one step (quick-add).
  createContact: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        company_ids: [event.params.id],
        custom: parseCustom(form.get("custom")),
      },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { contactAdded: true };
  },

  // Attach an existing contact person to this client.
  linkContact: async (event) => {
    const form = await event.request.formData();
    const contact_id = String(form.get("contact_id") ?? "").trim();
    if (!contact_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST("/api/v1/contacts/{contact_id}/links", {
      params: { path: { contact_id } },
      body: { company_id: event.params.id, is_primary: false },
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { contactLinked: true };
  },

  // Detach (never deletes the contact).
  unlinkContact: async (event) => {
    const form = await event.request.formData();
    const contact_id = String(form.get("contact_id") ?? "").trim();
    if (!contact_id) return fail(400, { error: "errors.required" });
    await apiFor(event).DELETE("/api/v1/contacts/{contact_id}/links/{company_id}", {
      params: { path: { contact_id, company_id: event.params.id } },
    });
    return { contactUnlinked: true };
  },

  setPrimaryContact: async (event) => {
    const form = await event.request.formData();
    const contact_id = String(form.get("contact_id") ?? "").trim();
    if (!contact_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).PATCH(
      "/api/v1/contacts/{contact_id}/links/{company_id}",
      {
        params: { path: { contact_id, company_id: event.params.id } },
        body: { is_primary: true },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { primarySet: true };
  },

  applyTemplate: async (event) => {
    const form = await event.request.formData();
    const template_id = String(form.get("template_id") ?? "");
    if (!template_id) return fail(400, { error: "errors.required" });
    const { error: apiError } = await apiFor(event).POST(
      "/api/v1/tasks/templates/{template_id}/apply",
      {
        params: { path: { template_id } },
        body: { company_id: event.params.id },
      },
    );
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });
    return { templateApplied: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/companies/{company_id}", {
      params: { path: { company_id: event.params.id } },
    });
    throw redirect(303, "/companies");
  },

  // Contactmomenten panel contract (lib/modules/interactions).
  ...interactionActions,
  // Drive panel contract (lib/modules/google).
  ...driveActions,
  // Marketing panel contract (lib/modules/marketing): link/unlink GA4/GSC/Ads accounts.
  ...marketingActions,
};
