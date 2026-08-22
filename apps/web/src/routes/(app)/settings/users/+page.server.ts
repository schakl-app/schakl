import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
// The employment-data actions (schedule, contracts, recurring, rate) are shared with the team
// leave roster, so they live in one place and can't drift (employment.server.ts).
import { availabilityActions, availabilityWindow } from "$lib/modules/leave/availability.server";
import { employmentActions } from "$lib/modules/leave/employment.server";
import { defaultSchedule } from "$lib/modules/leave/schedule";
import { portalActions } from "$lib/modules/portal/actions.server";
import { loadPortalLogins } from "$lib/modules/portal/load.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "members.member.read")) throw redirect(303, "/settings");

  // Work schedules are employment data, so they live on the person (#46) — but only when the
  // tenant runs `leave` and the caller may manage them. Two calls, not one per member: the
  // roster and the org default are what the whole list needs (docs/PERFORMANCE.md).
  const leaveEnabled = event.locals.theme?.enabledModules?.includes("leave") ?? false;
  const schedules = leaveEnabled && can(event.locals.user, "leave.profile.manage");
  // Hourly rates (#82) are salary-adjacent: a separate permission, shown only to someone who may
  // read anyone's rate. One roster call, like schedules (docs/PERFORMANCE.md).
  const rates = leaveEnabled && can(event.locals.user, "leave.rate.read", "any");
  const canEditRates = leaveEnabled && can(event.locals.user, "leave.rate.write");
  // Availability (freelance) has its own permission, not `leave.profile.manage`: keeping
  // somebody's calendar is a different act from rewriting the period they were engaged under.
  const availability = leaveEnabled && can(event.locals.user, "leave.availability.write", "any");

  const api = apiFor(event);
  // `/members` carries each membership's `role_ids`, so the effective set is derived here rather
  // than requested per member. The tenant's roles come from `settings/+layout.server.ts` — shared
  // with the Rollen screen and not refetched on tab navigation (docs/PERFORMANCE.md).
  const [
    members,
    portalLogins,
    profiles,
    settings,
    rateRows,
    contracts,
    recurring,
    leaveTypes,
    groupsRes,
    availabilityRows,
  ] = await Promise.all([
    api.GET("/api/v1/members"),
    // Klantlogins (#406): the *other* half of "who may sign in here". The portal module decides
    // its own permission, its own entitlement and whether the call is worth making at all —
    // the same three questions the card on a contact asks, so the two can never disagree.
    loadPortalLogins(api, { user: event.locals.user, theme: event.locals.theme }),
    schedules ? api.GET("/api/v1/leave/profiles") : Promise.resolve({ data: null }),
    schedules ? api.GET("/api/v1/leave/settings") : Promise.resolve({ data: null }),
    rates ? api.GET("/api/v1/leave/rates") : Promise.resolve({ data: null }),
    // Employment contracts (#65) — the whole roster in one call, like schedules. Read for
    // `availability` too: the ⋯ offers availability only on a freelance period, and that set is
    // derived from these rows — so a holder of `leave.availability.write:any` who lacked
    // `leave.profile.manage` got an empty set and therefore never got the menu item (#368).
    schedules || availability
      ? api.GET("/api/v1/leave/contracts", { params: { query: { all_users: true } } })
      : Promise.resolve({ data: null }),
    // Recurring free-day patterns (#107) — employment data, same home.
    schedules ? api.GET("/api/v1/leave/recurring") : Promise.resolve({ data: null }),
    schedules ? api.GET("/api/v1/leave/types") : Promise.resolve({ data: null }),
    // Company groups (#191): which memberships carry a visibility restriction, so the
    // roster can badge them — visible at a glance, per the issue. Manager-only fetch.
    can(event.locals.user, "companies.group.manage")
      ? api.GET("/api/v1/companies/groups")
      : Promise.resolve({ data: null }),
    // Availability exceptions for the whole roster, in the same read window the freelancer's
    // own page uses — one call, like every other lookup here (docs/PERFORMANCE.md).
    availability
      ? api.GET("/api/v1/leave/availability", {
          params: { query: { ...availabilityWindow(), all_users: true } },
        })
      : Promise.resolve({ data: null }),
  ]);
  const restrictedMembershipIds = [
    ...new Set((groupsRes.data ?? []).flatMap((g) => g.membership_ids ?? [])),
  ];

  return {
    members: members.data ?? [],
    portalLogins,
    restrictedMembershipIds,
    schedules,
    rates,
    canEditRates,
    availability,
    profiles: profiles.data ?? [],
    rateRows: rateRows.data ?? [],
    contracts: contracts.data ?? [],
    recurring: recurring.data ?? [],
    availabilityRows: availabilityRows.data ?? [],
    leaveTypes: leaveTypes.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
  };
};

export const actions: Actions = {
  // Work schedule, contracts, recurring free days and hourly rate — shared with the team leave
  // roster so both surfaces behave identically (employment.server.ts).
  ...employmentActions,
  // And the availability exceptions on top of them, shared with the freelancer's own /leave.
  ...availabilityActions,

  // Client-portal access, contributed by the portal module the way the contact's own page hosts
  // it (#193, #406). The subject arrives in the body rather than in the route: this screen is a
  // register of many logins, so the pressed row is the only thing that says which one.
  ...portalActions({
    subject: async (event) => {
      const form = await event.request.formData();
      return {
        entityType: String(form.get("entity_type") ?? ""),
        subjectId: String(form.get("subject_id") ?? ""),
        returnPath: "/settings/users",
      };
    },
  }),

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

  /**
   * The member's own account: their name, and whether they still work here.
   *
   * Two callers, one action, and the difference between them is what `absent means leave alone`
   * (§18) is for: the Bewerken dialog posts both fields, the ⋯ Deactiveren / Activeren item posts
   * only the status. So `full_name` is sent **only** when the form carried the input — a status
   * toggle that also posted an empty name would clear it on every use.
   */
  saveAccount: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("membership_id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });

    const body: { full_name?: string | null; active?: boolean } = {};
    if (form.has("full_name")) body.full_name = String(form.get("full_name") ?? "").trim();
    if (form.has("active")) body.active = String(form.get("active")) === "true";

    const { error } = await apiFor(event).PATCH("/api/v1/members/{membership_id}/account", {
      params: { path: { membership_id: id } },
      body,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { accountSaved: true };
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
