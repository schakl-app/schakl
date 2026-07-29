import { error as httpError, fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad, RequestEvent } from "./$types";

// Instance administrators (issue #26). Owner-only: managing who may cross tenants is
// deliberately not itself a capability, so a delegated admin cannot grant themselves more.
// This load mirrors the API's gate and the API remains the boundary — a delegated admin who
// navigated here directly gets 403 from the endpoint, and 404s out of the page.
export const load: PageServerLoad = async (event) => {
  const { data, error } = await apiFor(event).GET("/api/v1/instance/admins");
  if (error || !data) throw httpError(404);
  return { catalog: data.catalog, principals: data.principals };
};

function capabilitiesFrom(form: FormData): string[] {
  // Unchecked boxes are simply absent, so the posted set *is* the new set — which is what
  // makes unticking one a revocation rather than a no-op.
  return form.getAll("capabilities").map(String).filter(Boolean);
}

export const actions: Actions = {
  invite: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "").trim();
    if (!email) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/instance/admins", {
      body: {
        email,
        full_name: String(form.get("full_name") ?? "").trim() || null,
        capabilities: capabilitiesFrom(form),
      },
    });
    if (error) {
      const parsed = apiErrorKey(error);
      return fail(400, { error: parsed.fields?.capabilities ?? parsed.key });
    }
    return { invited: true };
  },

  update: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const { error } = await apiFor(event).PATCH("/api/v1/instance/admins/{user_id}", {
      params: { path: { user_id: userId } },
      body: { capabilities: capabilitiesFrom(form) },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  promote: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const { error } = await apiFor(event).PATCH("/api/v1/instance/admins/{user_id}", {
      params: { path: { user_id: userId } },
      body: { is_owner: true },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },

  revoke: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const { error } = await apiFor(event).DELETE("/api/v1/instance/admins/{user_id}", {
      params: { path: { user_id: userId } },
    });
    // The last-owner guard answers 409 here; surfacing its key tells the operator *why*
    // rather than showing a generic failure.
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
};
