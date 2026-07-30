import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/** The custom-domain wizard (#292): claim → prove ownership → point DNS → active.
 *  The load reads only persisted state (no DNS probes — SSR stays fast and the wizard
 *  resumes wherever the org left off); the `check` action is the probe the page polls. */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "settings.domain.write")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [{ data: domain }, { data: modules }, { data: tenant }] = await Promise.all([
    api.GET("/api/v1/meta/tenant/domain"),
    api.GET("/api/v1/meta/modules"),
    api.GET("/api/v1/meta/tenant"),
  ]);
  return {
    domain: domain ?? null,
    fallbackHost:
      tenant && modules ? `${tenant.slug}.${modules.base_domain}` : (tenant?.slug ?? ""),
  };
};

export const actions: Actions = {
  claim: async (event) => {
    const form = await event.request.formData();
    const domain = String(form.get("domain") ?? "")
      .trim()
      .toLowerCase();
    if (!domain) return fail(400, { error: "errors.required", claimError: true });
    const { data, error } = await apiFor(event).POST("/api/v1/meta/tenant/domain", {
      body: { domain },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.domain ?? parsed.key, claimError: true });
    }
    return { status: data };
  },
  check: async (event) => {
    const { data, error } = await apiFor(event).POST("/api/v1/meta/tenant/domain/check");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { report: data };
  },
  clear: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/meta/tenant/domain");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { cleared: true };
  },
};
