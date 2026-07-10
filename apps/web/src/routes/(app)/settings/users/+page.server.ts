import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "members.member.read")) throw redirect(303, "/");
  // The tenant's roles come from `settings/+layout.server.ts` — shared with the Rollen screen and
  // not refetched on tab navigation (docs/PERFORMANCE.md). `/members` carries each membership's
  // `role_ids`, so the effective set is derived here rather than requested per member.
  const { data } = await apiFor(event).GET("/api/v1/members");
  return { members: data ?? [] };
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

  /**
   * The whole role set, in one save. A membership may hold several roles and its permissions are
   * their union; release *N* additionally requires at least one system role, which the API
   * enforces and the form's `required` marker mirrors.
   */
  saveRoles: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("membership_id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const roleIds = form.getAll("role_ids").map(String).filter(Boolean);
    const { error } = await apiFor(event).PUT("/api/v1/members/{membership_id}/roles", {
      params: { path: { membership_id: id } },
      body: { role_ids: roleIds },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
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
