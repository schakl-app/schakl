import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked, excludedFrom, triflag } from "$lib/core/forms";
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

  const [profile, tones, templates, reports, contacts, company, sections, marketing] =
    await Promise.all([
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
      // For the display-name placeholder: the name a report carries when nobody overrides it.
      // A field whose "leave empty" behaviour is invisible is a field people fill in twice.
      api.GET("/api/v1/companies/{company_id}", { params: { path: { company_id } } }),
      // The section catalog (#373), so this client's own on/off list is built from the registry
      // rather than from a hardcoded list that would go stale the day a module ships a section.
      api.GET("/api/v1/reporting/templates/sections"),
      // Which sources this client actually has, so the picker can say whether a section will have
      // anything in it — and the resolved keyword-positions settings it inherits.
      api.GET("/api/v1/marketing/companies/{company_id}/settings", {
        params: { path: { company_id } },
      }),
    ]);

  return {
    companyId: company_id,
    companyName: company.data?.name ?? "",
    sections: sections.data ?? [],
    // `null` where the marketing module is off or the caller may not read it: the section
    // picker degrades to "no source hints" rather than to a 500.
    marketing: marketing.data ?? null,
    // The record this screen is about, under the key every other company page uses: the crumb row
    // names its `[id]` segment from it, and the trail hangs the next page off it. Already fetched
    // above for the display-name placeholder, so this costs nothing.
    company: company.data ?? null,
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
    // The keyword-positions block writes to *marketing*, so it is gated on the key that call
    // actually makes rather than on the one this screen is about (#310). A control that renders
    // for somebody whose save will 403 is a broken screen, and the 403 cannot explain itself.
    canManageMarketing: can(event.locals.user, "marketing.link.manage"),
    locale: event.locals.locale,
  };
};

/** `""` means *inherit* — the profile stays silent and the org default decides (§14's idiom). */
function inherited(raw: FormDataEntryValue | null): string | null {
  const value = String(raw ?? "").trim();
  return value || null;
}

/** A number field left blank means *inherit*, not zero. */
function inheritedNumber(raw: FormDataEntryValue | null): number | undefined {
  const value = String(raw ?? "").trim();
  if (!value) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * The section diff the picker posted (#373).
 *
 * One JSON field rather than a checkbox per section, and that is the point: a checkbox that is
 * not ticked posts *nothing*, so nine checkboxes could not tell "off for this client" apart
 * from "follow the template" — the very distinction the control exists to hold.
 */
function sectionOverrides(raw: FormDataEntryValue | null): Record<string, boolean> {
  try {
    const parsed = JSON.parse(String(raw ?? "{}")) as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(parsed)
        .filter(([, value]) => typeof value === "boolean")
        .map(([key, value]) => [key, value as boolean]),
    );
  } catch {
    return {};
  }
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
        display_name: inherited(form.get("display_name")),
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
          publish_to_portal: triflag(form, "publish_to_portal"),
        },
        sections: sectionOverrides(form.get("sections")),
        internal_enabled: checked(form, "internal_enabled"),
        active: checked(form, "active"),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });

    // Keyword-positions settings live on the *marketing* row, because that is the module that
    // owns rankings (CLAUDE.md §6) — but the choice belongs on the screen where somebody is
    // deciding what this client's report contains, so this form saves both. A failure here is
    // reported rather than swallowed: half a save that looks like a whole one is worse than an
    // error message.
    // Only where the caller may actually make that call — the block is not rendered otherwise,
    // and posting it anyway would turn a hidden control into a 403 on an unrelated save.
    if (!can(event.locals.user, "marketing.link.manage")) return { saved: true };
    const source = inherited(form.get("rankings_source"));
    const limit = inheritedNumber(form.get("rankings_limit"));
    const minImpressions = inheritedNumber(form.get("rankings_min_impressions"));
    const maxPosition = inheritedNumber(form.get("rankings_max_position"));
    const anyRanking =
      source !== null ||
      limit !== undefined ||
      minImpressions !== undefined ||
      maxPosition !== undefined;
    // Websites (#381). The block is only rendered for a client with more than one property, so
    // its absence means "this screen had nothing to say about it" and must leave the stored
    // override alone — not clear it. `report_all_links` is what tells the two apart, and it is
    // also what makes the exclusion complete: the ticked boxes say what is *in*, and a property
    // whose checkbox never rendered would otherwise silently stay in.
    const rendered = String(form.get("report_all_links") ?? "");
    const allLinks = rendered.split(",").map((id) => id.trim()).filter(Boolean);
    const split = inherited(form.get("report_split"));
    const exclude = excludedFrom(rendered, form.getAll("report_links"));
    const anyReport = allLinks.length > 0 && (split !== null || exclude.length > 0);
    const marketing = await apiFor(event).PUT("/api/v1/marketing/companies/{company_id}/settings", {
      params: { path: { company_id: event.params.id } },
      body: {
        // An explicit `null` is how "volg de standaard" is posted — omitting the key would mean
        // "leave alone", which cannot clear an override somebody is trying to remove (§18).
        rankings: anyRanking
          ? {
              source: source as "auto" | "seranking" | "search_console" | "off" | null,
              limit: limit ?? null,
              min_impressions: minImpressions ?? null,
              max_position: maxPosition ?? null,
            }
          : null,
        ...(allLinks.length
          ? {
              report: anyReport
                ? {
                    split: split as "per_website" | "combined" | null,
                    exclude,
                  }
                : null,
            }
          : {}),
      },
    });
    if (marketing.error) return fail(400, { error: apiErrorKey(marketing.error).key });
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
