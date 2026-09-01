import { error, fail, redirect } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { apiBaseUrl } from "$lib/core/api/client";
import { dedupeGets } from "$lib/core/api/dedupe";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { interactionActions } from "$lib/modules/interactions/actions.server";
import { driveActions } from "$lib/integrations/google/drive-actions.server";
import { fileActions } from "$lib/core/files/actions.server";
import { gtmActions } from "$lib/integrations/google_tag_manager/actions.server";
import { marketingActions } from "$lib/modules/marketing/actions.server";
import { timeEntryActions } from "$lib/modules/time/actions.server";

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
  const api = dedupeGets(apiFor(event));
  const company_id = event.params.id;

  // The entity rides *in* the fan, not in front of it (#290). Awaiting it first made every
  // other call wait a full round-trip for an answer none of them needed — they are all keyed by
  // the id in the URL, not by anything the company row says. The 404 check simply moves below;
  // the panels call answers 404 too if the id is not ours, so nothing leaks by asking.
  const [companyRes, panels, summary, definitions, templates, members] = await Promise.all([
    api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } }),
    api.GET("/api/v1/companies/{company_id}/panels", { params: { path: { company_id } } }),
    // The vital signs (#364) — the strip under the header that answers "are we all right with
    // this client" before anything is scrolled. In the same fan as the panels: it is keyed by
    // the id in the URL like everything else here, so it costs no extra round trip.
    api.GET("/api/v1/companies/{company_id}/summary", { params: { path: { company_id } } }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "company" } },
    }),
    // The template applier only renders for holders of the permission (#253), so a viewer
    // who can't apply one shouldn't pay for the fetch either.
    can(event.locals.user, "tasks.template.apply")
      ? api.GET("/api/v1/tasks/templates").then((r) => r.data ?? [])
      : [],
    api.GET("/api/v1/members/lookup"),
  ]);
  const company = companyRes.data;
  if (!company) throw error(404, { code: "not_found", message: "errors.not_found" });

  // The edit modal's own lookups stream in behind the page (the `createForm` pattern): nothing
  // on the client page draws them, and most visits never open the modal. One call covers both
  // jobs — each `ContactRead` carries the companies it is linked to with the per-company
  // `is_primary`, so the client's current contacts are a filter over this list rather than a
  // second request (docs/PERFORMANCE.md).
  const editForm = Promise.all([
    api.GET("/api/v1/contacts", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "first_name" } },
    }),
    api.GET("/api/v1/custom-fields/definitions", {
      params: { query: { entity_type: "contact" } },
    }),
  ])
    .then(([contacts, contactDefinitions]) => ({
      contacts: contacts.data?.items ?? [],
      contactDefinitions: contactDefinitions.data ?? [],
    }))
    .catch(() => ({ contacts: [], contactDefinitions: [] }));

  return {
    company,
    panels: panels.data ?? [],
    summary: summary.data ?? [],
    definitions: definitions.data ?? [],
    templates,
    members: members.data ?? [],
    editForm,
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();

    // **Only what the form actually carried** (#364). The edit surface is no longer one
    // 30-field dialog: the Gegevens card and the Factuurgegevens card each flip into edit mode
    // on their own, and the status pill posts one field from the header. Reading every field
    // with `?? ""` would have made each of those a *wholesale* write that nulled everything the
    // section left out — the same shape as a permission-hidden block being wiped by a restricted
    // caller's ordinary save. `has()` is the question, and asking it any other way is the bug.
    const text = (field: string): string | null | undefined =>
      form.has(field) ? String(form.get(field) ?? "").trim() || null : undefined;

    // A form that carries `name` must carry a real one; one that doesn't isn't renaming.
    if (form.has("name") && !String(form.get("name") ?? "").trim()) {
      return fail(400, { error: "errors.required" });
    }

    const api = apiFor(event);
    const company_id = event.params.id;
    const country = text("country");
    const body = {
      name: text("name") ?? undefined,
      legal_name: text("legal_name"),
      client_number: text("client_number"),
      website: text("website"),
      phone: text("phone"),
      invoice_email: text("invoice_email"),
      vat_number: text("vat_number"),
      coc_number: text("coc_number"),
      address_line1: text("address_line1"),
      house_number: text("house_number"),
      address_line2: text("address_line2"),
      postal_code: text("postal_code"),
      city: text("city"),
      country: country ? country.toUpperCase() : country,
      notes: text("notes"),
      status: form.has("status") ? (String(form.get("status")) as "active") : undefined,
      assignees: form.has("assignees") ? parseAssignees(form.get("assignees")) : undefined,
      custom: form.has("custom") ? parseCustom(form.get("custom")) : undefined,
    };
    // `undefined` keys vanish in `JSON.stringify`, and the API reads `exclude_unset`: absent
    // means leave alone, an explicit `null` clears. That is the same rule bulk edit states
    // (CLAUDE.md §18), and it is what makes a per-section save safe.
    const { error: apiError } = await api.PATCH("/api/v1/companies/{company_id}", {
      params: { path: { company_id } },
      body,
    });
    if (apiError) return fail(400, { error: apiErrorKey(apiError).key });

    // Per-client logo (#196): a chosen file replaces it; the checkbox removes it. Multipart
    // goes through a plain fetch — the typed client has no multipart serializer.
    const logoFile = form.get("logo_file");
    if (logoFile instanceof File && logoFile.size > 0) {
      const body = new FormData();
      body.append("file", logoFile, logoFile.name);
      const res = await event.fetch(`${apiBaseUrl()}/api/v1/companies/${company_id}/logo`, {
        method: "POST",
        headers: {
          cookie: event.request.headers.get("cookie") ?? "",
          "x-forwarded-host": event.request.headers.get("host") ?? "",
        },
        body,
      });
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
  // Drive panel contract (lib/integrations/google).
  ...driveActions,
  // Documents pinned to the client (core storage): the hub's files panel posts here.
  ...fileActions("company"),
  // Marketing panel contract (lib/modules/marketing): link/unlink GA4/GSC/Ads accounts.
  ...marketingActions,
  // Tag Manager panel contract (lib/integrations/google_tag_manager): attach a container.
  ...gtmActions,
  // Uren panel contract (lib/modules/time): correct or remove one registration from the ⋯ (#400).
  ...timeEntryActions,
};
