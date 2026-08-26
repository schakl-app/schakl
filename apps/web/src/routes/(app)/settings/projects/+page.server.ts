import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Projecten: the budget alert. Global on purpose — "when is a budget almost
// reached" is one answer per agency, not sixty per-project knobs. Admin-only
// (projects.settings.manage); the nightly watch reads the same row.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "projects.settings.manage")) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/projects/settings");
  return { settings: data ?? null };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    // Presence, never a literal comparison (`checked`) — the silent-false checkbox bug this
    // codebase has already paid for once.
    const threshold = Number(form.get("budget_alert_threshold"));
    const { error } = await apiFor(event).PUT("/api/v1/projects/settings", {
      body: {
        budget_alert_emails: checked(form, "budget_alert_emails"),
        budget_alert_threshold: Number.isFinite(threshold) && threshold > 0 ? threshold : null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
