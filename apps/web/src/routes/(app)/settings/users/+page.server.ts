import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
// The employment-data actions (schedule, contracts, recurring, rate) are shared with the team
// leave roster, so they live in one place and can't drift (employment.server.ts).
import { employmentActions } from "$lib/modules/leave/employment.server";
import { defaultSchedule } from "$lib/modules/leave/schedule";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "members.member.read")) throw redirect(303, "/");

  // Work schedules are employment data, so they live on the person (#46) — but only when the
  // tenant runs `leave` and the caller may manage them. Two calls, not one per member: the
  // roster and the org default are what the whole list needs (docs/PERFORMANCE.md).
  const leaveEnabled = event.locals.theme?.enabledModules?.includes("leave") ?? false;
  const schedules = leaveEnabled && can(event.locals.user, "leave.profile.manage");
  // Hourly rates (#82) are salary-adjacent: a separate permission, shown only to someone who may
  // read anyone's rate. One roster call, like schedules (docs/PERFORMANCE.md).
  const rates = leaveEnabled && can(event.locals.user, "leave.rate.read", "any");
  const canEditRates = leaveEnabled && can(event.locals.user, "leave.rate.write");

  const api = apiFor(event);
  // `/members` carries each membership's `role_ids`, so the effective set is derived here rather
  // than requested per member. The tenant's roles come from `settings/+layout.server.ts` — shared
  // with the Rollen screen and not refetched on tab navigation (docs/PERFORMANCE.md).
  const [members, profiles, settings, rateRows, contracts, recurring, leaveTypes, groupsRes] =
    await Promise.all([
      api.GET("/api/v1/members"),
      schedules ? api.GET("/api/v1/leave/profiles") : Promise.resolve({ data: null }),
      schedules ? api.GET("/api/v1/leave/settings") : Promise.resolve({ data: null }),
      rates ? api.GET("/api/v1/leave/rates") : Promise.resolve({ data: null }),
      // Employment contracts (#65) — the whole roster in one call, like schedules.
      schedules
        ? api.GET("/api/v1/leave/contracts", { params: { query: { all_users: true } } })
        : Promise.resolve({ data: null }),
      // Recurring rostered-free-day patterns (#107) — employment data, same home.
      schedules ? api.GET("/api/v1/leave/recurring") : Promise.resolve({ data: null }),
      schedules ? api.GET("/api/v1/leave/types") : Promise.resolve({ data: null }),
      // Company groups (#191): which memberships carry a visibility restriction, so the
      // roster can badge them — visible at a glance, per the issue. Manager-only fetch.
      can(event.locals.user, "companies.group.manage")
        ? api.GET("/api/v1/companies/groups")
        : Promise.resolve({ data: null }),
    ]);
  const restrictedMembershipIds = [
    ...new Set((groupsRes.data ?? []).flatMap((g) => g.membership_ids ?? [])),
  ];

  return {
    members: members.data ?? [],
    restrictedMembershipIds,
    schedules,
    rates,
    canEditRates,
    profiles: profiles.data ?? [],
    rateRows: rateRows.data ?? [],
    contracts: contracts.data ?? [],
    recurring: recurring.data ?? [],
    leaveTypes: leaveTypes.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
  };
};

export const actions: Actions = {
  // Work schedule, contracts, recurring free days and hourly rate — shared with the team leave
  // roster so both surfaces behave identically (employment.server.ts).
  ...employmentActions,

  invite: async (event) => {
    const form = await event.request.formData();
    const email = String(form.get("email") ?? "").trim();
    if (!email) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/members/invite", {
      body: {
        email,
        full_name: String(form.get("full_name") ?? "").trim() || null,
        role: String(form.get("role") ?? "member") as "member",
        send_email: form.get("send_email") !== null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return {
      invited: true,
      // #161: the admin must know when the welcome mail could not go out (no transport).
      inviteEmailSent: data?.invite_email_sent ?? null,
      inviteEmailError: data?.invite_email_error ?? null,
    };
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

  /** Reset a member's 2FA — the lost-phone escape hatch (docs/TWOFACTOR.md); audited API-side. */
  resetTwoFactor: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("membership_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/members/{membership_id}/two-factor", {
        params: { path: { membership_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { twoFactorReset: true };
  },
};
