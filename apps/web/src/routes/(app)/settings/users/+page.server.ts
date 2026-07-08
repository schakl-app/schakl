import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

const ROLES = ["owner", "admin", "member", "client"] as const;

export const load: PageServerLoad = async (event) => {
  // Manager-only screen (the API enforces this too).
  if (!event.locals.user?.canManage) throw redirect(303, "/");
  const { data } = await apiFor(event).GET("/api/v1/members");
  return { members: data ?? [], roles: ROLES };
};

export const actions: Actions = {
  invite: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "").trim();
    if (!email) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/members/invite", {
      body: {
        email,
        full_name: String(form.get("full_name") ?? "").trim() || null,
        role: String(form.get("role") ?? "member") as "member",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { invited: true };
  },

  changeRole: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("membership_id") ?? "");
    const role = String(form.get("role") ?? "member") as "member";
    if (id) {
      const { error } = await apiFor(event).PATCH("/api/v1/members/{membership_id}", {
        params: { path: { membership_id: id } },
        body: { role },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { changed: true };
  },

  revoke: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("membership_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/members/{membership_id}", {
        params: { path: { membership_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { revoked: true };
  },
};
