import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instance API keys (epic #199): the credentials the operator's own machinery uses on the
// provisioning API. The secret is returned exactly once by the create call.
export const load: PageServerLoad = async (event) => {
  const { data } = await apiFor(event).GET("/api/v1/instance/api-keys");
  return { keys: data ?? [] };
};

export const actions: Actions = {
  create: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/instance/api-keys", {
      body: { name },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    return { secret: data.secret };
  },

  revoke: async (event) => {
    const form = await event.request.formData();
    const keyId = String(form.get("key_id") ?? "");
    const { error } = await apiFor(event).POST("/api/v1/instance/api-keys/{key_id}/revoke", {
      params: { path: { key_id: keyId } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { revoked: true };
  },
};
