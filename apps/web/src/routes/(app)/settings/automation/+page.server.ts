import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "automation.rule.read")) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/automation/rules");
  return {
    rules: data ?? [],
    canWrite: can(event.locals.user, "automation.rule.write"),
    canReadRuns: can(event.locals.user, "automation.run.read"),
  };
};

export const actions: Actions = {
  // Inline enabled toggle — a reversible switch, so it stays in the row (docs/UX.md).
  toggle: async (event) => {
    const form = await event.request.formData();
    const rule_id = String(form.get("id") ?? "");
    if (!rule_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/automation/rules/{rule_id}", {
      params: { path: { rule_id } },
      body: { enabled: String(form.get("enabled")) === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { toggled: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const rule_id = String(form.get("id") ?? "");
    if (rule_id) {
      await apiFor(event).DELETE("/api/v1/automation/rules/{rule_id}", {
        params: { path: { rule_id } },
      });
    }
    return { deleted: true };
  },
};
