import "$lib/modules"; // ensure the panels are registered before we read the registry

import { error, fail, redirect } from "@sveltejs/kit";

import { parseAssignees } from "$lib/core/assignees";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import { interactionActions } from "$lib/modules/interactions/actions.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const contact_id = event.params.id;

  // Panels contributed to a contact (CLAUDE.md §6) — today just the core activity trail (#67).
  // A contact has no aggregate period, so `periodStart` is null.
  const context = { entityId: contact_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "contact");

  // Portal state (#193): manager-only (managing logins is member management), one call.
  const canPortal = can(event.locals.user, "members.member.write");
  const [contact, definitions, companies, companyDefinitions, members, portal, ...panelData] =
    await Promise.all([
      api.GET("/api/v1/contacts/{contact_id}", { params: { path: { contact_id } } }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "contact" } },
      }),
      api.GET("/api/v1/companies", { params: { query: { limit: 200, offset: 0 } } }),
      api.GET("/api/v1/custom-fields/definitions", {
        params: { query: { entity_type: "company" } },
      }),
      // The quick-create client dialog is the full client form, so it needs the member lookup.
      api.GET("/api/v1/members/lookup"),
      canPortal
        ? api.GET("/api/v1/contacts/{contact_id}/portal", { params: { path: { contact_id } } })
        : Promise.resolve({ data: null }),
      ...panels.map((panel) => panel.load(api, context)),
    ]);
  if (!contact.data) throw error(404, { code: "not_found", message: "errors.not_found" });
  return {
    contact: contact.data,
    definitions: definitions.data ?? [],
    companies: companies.data?.items ?? [],
    companyDefinitions: companyDefinitions.data ?? [],
    members: members.data ?? [],
    portal: portal.data ?? null,
    canPortal,
    context,
    panels: panels.map((panel, index) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      data: panelData[index],
    })),
    locale: event.locals.locale,
  };
};

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const actions: Actions = {
  update: async (event) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { error: "errors.required" });

    const { error: err } = await apiFor(event).PATCH("/api/v1/contacts/{contact_id}", {
      params: { path: { contact_id: event.params.id } },
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (err) {
      const e = apiErrorKey(err);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  // Attach this contact to an existing client.
  linkCompany: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { error: "errors.required" });
    const { error: err } = await apiFor(event).POST("/api/v1/contacts/{contact_id}/links", {
      params: { path: { contact_id: event.params.id } },
      body: { company_id, is_primary: false },
    });
    if (err) return fail(400, { error: apiErrorKey(err).key });
    return { linked: true };
  },

  unlinkCompany: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { error: "errors.required" });
    await apiFor(event).DELETE("/api/v1/contacts/{contact_id}/links/{company_id}", {
      params: { path: { contact_id: event.params.id, company_id } },
    });
    return { unlinked: true };
  },

  setPrimaryCompany: async (event) => {
    const form = await event.request.formData();
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { error: "errors.required" });
    const { error: err } = await apiFor(event).PATCH(
      "/api/v1/contacts/{contact_id}/links/{company_id}",
      {
        params: { path: { contact_id: event.params.id, company_id } },
        body: { is_primary: true },
      },
    );
    if (err) return fail(400, { error: apiErrorKey(err).key });
    return { primarySet: true };
  },

  // Create a new client and attach it to this contact in one step.
  createCompany: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { data: company, error: err } = await apiFor(event).POST("/api/v1/companies", {
      body: {
        name,
        website: String(form.get("website") ?? "").trim() || null,
        notes: String(form.get("notes") ?? "").trim() || null,
        status: String(form.get("status") ?? "active") as "active",
        assignees: parseAssignees(form.get("assignees")),
        custom: parseCustom(form.get("custom")),
      },
    });
    if (err || !company) return fail(400, { error: err ? apiErrorKey(err).key : "errors.unknown" });
    const { error: linkErr } = await apiFor(event).POST("/api/v1/contacts/{contact_id}/links", {
      params: { path: { contact_id: event.params.id } },
      body: { company_id: company.id, is_primary: false },
    });
    if (linkErr) return fail(400, { error: apiErrorKey(linkErr).key });
    return { companyCreated: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/contacts/{contact_id}", {
      params: { path: { contact_id: event.params.id } },
    });
    throw redirect(303, "/contacts");
  },

  // Client portal (#193): enable (invite), resend, disable — the API is the boundary.
  portalEnable: async (event) => {
    const { data, error: err } = await apiFor(event).POST(
      "/api/v1/contacts/{contact_id}/portal",
      { params: { path: { contact_id: event.params.id } } },
    );
    if (err) return fail(400, { portalError: apiErrorKey(err).fields?.email ?? apiErrorKey(err).key });
    return { portalSaved: true, portalEmail: data?.invite_email_sent ?? null };
  },

  portalResend: async (event) => {
    const { data, error: err } = await apiFor(event).POST(
      "/api/v1/contacts/{contact_id}/portal/resend",
      { params: { path: { contact_id: event.params.id } } },
    );
    if (err) return fail(400, { portalError: apiErrorKey(err).key });
    return { portalSaved: true, portalEmail: data?.invite_email_sent ?? null };
  },

  portalDisable: async (event) => {
    const { error: err } = await apiFor(event).DELETE(
      "/api/v1/contacts/{contact_id}/portal",
      { params: { path: { contact_id: event.params.id } } },
    );
    if (err) return fail(400, { portalError: apiErrorKey(err).key });
    return { portalSaved: true };
  },

  // Contactmomenten panel contract (lib/modules/interactions).
  ...interactionActions,
};
