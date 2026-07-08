import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { LOCALES } from "$lib/core/i18n";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Personal account — reachable by every member (NOT manager-gated, unlike org settings).
export const load: PageServerLoad = async (event) => {
  return {
    account: event.locals.user,
    locales: LOCALES,
    currentLocale: event.locals.locale,
  };
};

export const actions: Actions = {
  updateProfile: async (event) => {
    const form = await event.request.formData();
    const full_name = String(form.get("full_name") ?? "").trim();
    const { error } = await apiFor(event).PATCH("/api/v1/meta/me", {
      body: { full_name: full_name || null },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
};
