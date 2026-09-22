import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → Vergaderingen: the consent statement before a recording, what the minutes
 * document looks like, and the house rules the minutes are written to. Admin-only
 * (`meetings.settings.manage`); the recorder, the worker and the renderer read the same row.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meetings.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [settings, sections] = await Promise.all([
    api.GET("/api/v1/meetings/settings"),
    api.GET("/api/v1/meetings/settings/sections"),
  ]);
  return { settings: settings.data ?? null, sections: sections.data ?? [] };
};

export const actions: Actions = {
  /** One form, one row: every field the screen draws is posted, so nothing is left as it was
   *  by accident — and a checkbox is read by presence (`checked`), never by value. */
  save: async (event) => {
    const form = await event.request.formData();
    const text = (name: string) => String(form.get(name) ?? "").trim();
    const sections = form
      .getAll("document_sections")
      .map((value) => String(value))
      .filter(Boolean);
    const { error } = await apiFor(event).PUT("/api/v1/meetings/settings", {
      body: {
        consent_required: checked(form, "consent_required"),
        ai_instructions: text("ai_instructions") || null,
        document_design: text("document_design") || "standard",
        document_accent_color: text("document_accent_color") || null,
        document_cover_file_id: text("document_cover_file_id") || null,
        document_footer_text: text("document_footer_text") || null,
        document_avatars: checked(form, "document_avatars"),
        document_sections: sections,
        document_custom_html: text("document_custom_html") || null,
        document_custom_css: text("document_custom_css") || null,
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.fields?.html ?? e.key });
    }
    return { saved: true };
  },
};
