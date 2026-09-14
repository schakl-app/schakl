import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Taken: the e-mail intake address (taak@bureau.nl) and what a mailed task gets
// by default. Admin-only (tasks.settings.manage); the mailbox feeds read the same row.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "tasks.settings.manage")) throw redirect(303, "/settings");
  const { data } = await apiFor(event).GET("/api/v1/tasks/settings");
  return { settings: data ?? null };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const address = String(form.get("intake_address") ?? "").trim();
    const days = Number(form.get("intake_default_due_days"));
    const { error } = await apiFor(event).PUT("/api/v1/tasks/settings", {
      body: {
        // An emptied box switches the intake off: explicit null, never an absent field (§18).
        intake_address: address || null,
        intake_default_due_days: Number.isFinite(days) && days >= 0 ? days : null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
