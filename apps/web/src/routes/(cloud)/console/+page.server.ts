import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Console home (epic #199): every org on this cloud instance, plus the instance audit
// trail. Access is enforced API-side; the layout guard mirrors it for UX.
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const [orgs, audit] = await Promise.all([
    api.GET("/api/v1/instance/orgs"),
    api.GET("/api/v1/instance/audit", { params: { query: { limit: 25 } } }),
  ]);
  return { orgs: orgs.data ?? [], audit: audit.data ?? [] };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const slug = String(form.get("slug") ?? "")
      .trim()
      .toLowerCase();
    const ownerEmail = String(form.get("owner_email") ?? "").trim();
    const plan = String(form.get("plan") ?? "trial");
    const trialDays = Number(form.get("trial_days") ?? "") || null;
    if (!name || !slug) return fail(400, { error: "errors.required" });

    const api = apiFor(event);
    const created = await api.POST("/api/v1/instance/orgs", {
      body: { name, slug, owner_email: ownerEmail || null },
    });
    if (created.error) {
      const parsed = apiErrorKey(created.error);
      return fail(400, { error: parsed.fields?.slug ?? parsed.key });
    }
    // The console always assigns a plan (this is the operator's billing state, #200): a
    // clocked trial by default, or standard / unlimited ("no expiration") on choice.
    const { error } = await api.PATCH("/api/v1/instance/orgs/{org_id}/plan", {
      params: { path: { org_id: created.data.id } },
      body: { plan, trial_days: plan === "trial" ? trialDays : null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { created: true };
  },
};
