import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * A client's reporting profile (issue #300) — what is true about them, who receives the report,
 * and when.
 *
 * On the client's own page rather than in Instellingen, because that is what it is about: the
 * house voice and the templates are org configuration, this is a fact about one customer
 * (docs/UX.md). It is also the screen that replaces the "Klantenoverzicht" spreadsheet — minus
 * every column the CRM already holds.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "reporting.profile.manage")) {
    throw redirect(303, `/companies/${event.params.id}`);
  }
  const api = apiFor(event);
  const company_id = event.params.id;

  const [profile, tones, templates, reports, contacts] = await Promise.all([
    api.GET("/api/v1/reporting/companies/{company_id}/profile", {
      params: { path: { company_id } },
    }),
    api.GET("/api/v1/reporting/tones"),
    api.GET("/api/v1/reporting/templates"),
    api.GET("/api/v1/reporting/reports", {
      params: { query: { company_id, limit: 12, count: false } },
    }),
    // The client's own people, so recipients are picked rather than typed. A typed address is
    // a typo waiting to send a client's report to nobody.
    api.GET("/api/v1/contacts", {
      params: { query: { company_id, limit: 100, meta: false, count: false } },
    }),
  ]);

  return {
    companyId: company_id,
    profile: profile.data ?? null,
    tones: tones.data ?? [],
    templates: templates.data ?? [],
    reports: reports.data?.items ?? [],
    contacts: (contacts.data?.items ?? [])
      .filter((c) => Boolean(c.email))
      .map((c) => ({
        id: c.id,
        email: String(c.email),
        name: [c.first_name, c.last_name].filter(Boolean).join(" ") || String(c.email),
      })),
    canWrite: can(event.locals.user, "reporting.report.write"),
    locale: event.locals.locale,
  };
};

/** `""` means *inherit* — the profile stays silent and the org default decides (§14's idiom). */
function inherited(raw: FormDataEntryValue | null): string | null {
  const value = String(raw ?? "").trim();
  return value || null;
}

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const contacts = form.getAll("recipient").map(String);
    // Picked contacts first, then anything typed by hand — the same list, one shape.
    const extra = String(form.get("extra_recipients") ?? "")
      .split(/[\n,;]/)
      .map((value) => value.trim())
      .filter((value) => value.includes("@"));

    const cadence = inherited(form.get("cadence"));
    const delivery = inherited(form.get("delivery"));

    const { error } = await apiFor(event).PUT("/api/v1/reporting/companies/{company_id}/profile", {
      params: { path: { company_id: event.params.id } },
      body: {
        tone_id: inherited(form.get("tone_id")),
        template_id: inherited(form.get("template_id")),
        internal_template_id: inherited(form.get("internal_template_id")),
        locale: String(form.get("locale") ?? "nl"),
        business_context: inherited(form.get("business_context")),
        goals: inherited(form.get("goals")),
        seo_focus: inherited(form.get("seo_focus")),
        sea_focus: inherited(form.get("sea_focus")),
        key_services: inherited(form.get("key_services")),
        priority_pages: inherited(form.get("priority_pages")),
        conversion_goals: inherited(form.get("conversion_goals")),
        scope_notes: inherited(form.get("scope_notes")),
        avoid_topics: inherited(form.get("avoid_topics")),
        recipients: [
          ...contacts.map((raw) => {
            const [id, email, name] = raw.split("|");
            return { contact_id: id || null, email: email ?? "", name: name ?? "" };
          }),
          ...extra.map((email) => ({ contact_id: null, email, name: "" })),
        ],
        schedule: {
          cadence: cadence as "off" | "monthly" | "quarterly" | null,
          day_of_month: Number(form.get("day_of_month")) || null,
          hour: form.get("hour") === "" ? null : Number(form.get("hour")),
          compare: inherited(form.get("compare")) as "year" | "previous" | null,
          delivery: delivery as "review" | "auto" | null,
          publish_to_portal:
            form.get("publish_to_portal_set") === "on"
              ? form.get("publish_to_portal") === "on"
              : null,
        },
        internal_enabled: form.get("internal_enabled") === "on",
        active: form.get("active") === "on",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  generate: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).POST("/api/v1/reporting/reports/generate", {
      body: {
        company_id: event.params.id,
        audience: String(form.get("audience") ?? "client") as "client" | "internal",
        refresh_data: false,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { queued: true };
  },
};
