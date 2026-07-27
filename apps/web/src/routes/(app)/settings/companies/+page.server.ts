import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "companies.settings.manage")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/companies/settings");
  return { settings: data ?? null };
};

export const actions: Actions = {
  saveNumbering: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/companies/settings", {
      body: {
        client_number_format: String(form.get("client_number_format") ?? "").trim(),
        client_number_next_seq: Number(form.get("client_number_next_seq") ?? 1) || 1,
        // Unchecked checkboxes are simply absent from the body, so read presence, not value.
        client_number_reset_yearly: form.get("client_number_reset_yearly") === "1",
        client_number_auto: form.get("client_number_auto") === "1",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  backfill: async (event) => {
    const { data, error } = await apiFor(event).POST(
      "/api/v1/companies/settings/backfill-client-numbers",
      {},
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { numbered: data?.numbered ?? 0 };
  },
};
