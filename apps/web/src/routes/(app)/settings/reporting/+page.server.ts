import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Rapportage (issue #300): the house voice, the document templates and the
 * org-wide schedule every client inherits.
 *
 * A **client's own** profile is deliberately not here — it belongs on the client's page, beside
 * everything else about them (docs/UX.md: org configuration lives in Instellingen, per-record
 * configuration lives on the record).
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "reporting.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [tones, templates, settings, sections] = await Promise.all([
    api.GET("/api/v1/reporting/tones"),
    api.GET("/api/v1/reporting/templates"),
    api.GET("/api/v1/reporting/settings"),
    api.GET("/api/v1/reporting/templates/sections"),
  ]);
  return {
    tones: tones.data ?? [],
    templates: templates.data ?? [],
    settings: settings.data ?? null,
    sections: sections.data ?? [],
  };
};

/** Split a textarea of one-phrase-per-line into the list the API stores. */
function lines(raw: FormDataEntryValue | null): string[] {
  return String(raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function toneBody(form: FormData) {
  return {
    name: String(form.get("name") ?? "").trim(),
    description: String(form.get("description") ?? "").trim() || null,
    instructions: String(form.get("instructions") ?? ""),
    banned_phrases: lines(form.get("banned_phrases")),
    preferred_phrases: lines(form.get("preferred_phrases")),
    is_default: form.get("is_default") === "on",
    active: form.get("active") !== "off",
    position: Number(form.get("position") ?? 0) || 0,
  };
}

export const actions: Actions = {
  saveSettings: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/reporting/settings", {
      body: {
        default_locale: String(form.get("default_locale") ?? "nl"),
        footer_text: String(form.get("footer_text") ?? "").trim() || null,
        schedule: {
          cadence: String(form.get("cadence") ?? "monthly") as "off" | "monthly" | "quarterly",
          day_of_month: Number(form.get("day_of_month") ?? 5) || 5,
          hour: Number(form.get("hour") ?? 8) || 0,
          compare: String(form.get("compare") ?? "year") as "year" | "previous",
          delivery: String(form.get("delivery") ?? "review") as "review" | "auto",
          publish_to_portal: form.get("publish_to_portal") === "on",
        },
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { savedSettings: true };
  },

  saveTone: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const api = apiFor(event);
    const body = toneBody(form);
    if (!body.name) return fail(400, { error: "errors.validation" });
    const { error } = id
      ? await api.PUT("/api/v1/reporting/tones/{tone_id}", {
          params: { path: { tone_id: id } },
          body,
        })
      : await api.POST("/api/v1/reporting/tones", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { savedTone: true, created: !id };
  },

  deleteTone: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE("/api/v1/reporting/tones/{tone_id}", {
      params: { path: { tone_id: String(form.get("id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deletedTone: true };
  },

  saveTemplate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const api = apiFor(event);
    // The layout the section editor posted: an ordered list of `{key, enabled}`. A key the
    // catalog never offered is dropped server-side; what matters here is that a section the
    // form never mentions stays *unmentioned*, so a later release's new section still appears.
    let layout: {
      sections: { key: string; enabled: boolean; label_i18n: Record<string, string> }[];
    };
    try {
      const parsed = JSON.parse(String(form.get("layout") ?? "[]")) as {
        key: string;
        enabled: boolean;
      }[];
      layout = {
        sections: parsed.map((entry) => ({
          key: String(entry.key),
          enabled: entry.enabled !== false,
          label_i18n: {},
        })),
      };
    } catch {
      layout = { sections: [] };
    }
    // The PUT is wholesale, and this form edits four of a template's ten fields — so anything
    // it does not draw a control for has to be *carried*, not defaulted. Sending `design:
    // "standard"` and `custom_html: null` because the form has no such input reads to the API
    // as a deliberate "throw the tenant's own design away", and renaming a template silently
    // did exactly that. Absent means unchanged; only a control the user actually saw may clear
    // a field.
    const current = id
      ? ((await api.GET("/api/v1/reporting/templates", {})).data ?? []).find((t) => t.id === id)
      : undefined;
    const body = {
      name: String(form.get("name") ?? "").trim(),
      audience: String(form.get("audience") ?? "client") as "client" | "internal",
      design: current?.design ?? "standard",
      layout,
      custom_html: current?.custom_html ?? null,
      custom_css: current?.custom_css ?? null,
      accent_color: String(form.get("accent_color") ?? "").trim() || null,
      cover_image_file_id: current?.cover_image_file_id ?? null,
      intro_text: String(form.get("intro_text") ?? "").trim() || null,
      is_default: form.get("is_default") === "on",
    };
    if (!body.name) return fail(400, { error: "errors.validation" });
    const { error } = id
      ? await api.PUT("/api/v1/reporting/templates/{template_id}", {
          params: { path: { template_id: id } },
          body,
        })
      : await api.POST("/api/v1/reporting/templates", { body });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { savedTemplate: true, created: !id };
  },

  deleteTemplate: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).DELETE("/api/v1/reporting/templates/{template_id}", {
      params: { path: { template_id: String(form.get("id") ?? "") } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deletedTemplate: true };
  },
};
