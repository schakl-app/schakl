import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { defaultSchedule, type WorkSchedule } from "$lib/modules/leave/schedule";

import type { Actions, PageServerLoad } from "./$types";

/** The editor posts the whole week as one JSON field; the API validates every rule again. */
function parseSchedule(raw: FormDataEntryValue | null): WorkSchedule | null {
  try {
    return JSON.parse(String(raw ?? "")) as WorkSchedule;
  } catch {
    return null;
  }
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "members.member.read")) throw redirect(303, "/");

  // Work schedules are employment data, so they live on the person (#46) — but only when the
  // tenant runs `leave` and the caller may manage them. Two calls, not one per member: the
  // roster and the org default are what the whole list needs (docs/PERFORMANCE.md).
  const schedules =
    (event.locals.theme?.enabledModules?.includes("leave") ?? false) &&
    can(event.locals.user, "leave.profile.manage");

  const api = apiFor(event);
  // `/members` carries each membership's `role_ids`, so the effective set is derived here rather
  // than requested per member. The tenant's roles come from `settings/+layout.server.ts` — shared
  // with the Rollen screen and not refetched on tab navigation (docs/PERFORMANCE.md).
  const [members, profiles, settings] = await Promise.all([
    api.GET("/api/v1/members"),
    schedules ? api.GET("/api/v1/leave/profiles") : Promise.resolve({ data: null }),
    schedules ? api.GET("/api/v1/leave/settings") : Promise.resolve({ data: null }),
  ]);

  return {
    members: members.data ?? [],
    schedules,
    profiles: profiles.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
  };
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

  /**
   * This person's week (#46), or `null` to follow the org default. `hours_per_week` is derived
   * from it by the API and never posted from here.
   */
  saveSchedule: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    if (!userId) return fail(400, { error: "errors.required" });
    const inherit = form.get("inherit") === "true";
    const schedule = inherit ? null : parseSchedule(form.get("schedule"));
    if (!inherit && !schedule) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).PUT("/api/v1/leave/profiles/{user_id}", {
      params: { path: { user_id: userId } },
      body: { schedule },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { scheduleSaved: true };
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
