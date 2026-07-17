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

  /**
   * This person's hourly rate (#82), or `null` to clear it. Admin-only (`leave.rate.write`),
   * which the API re-enforces; the empty field means "no rate recorded".
   */
  saveRate: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    if (!userId) return fail(400, { error: "errors.required" });
    const raw = String(form.get("hourly_rate") ?? "")
      .trim()
      .replace(",", ".");
    const hourly_rate = raw === "" ? null : raw;
    const { error } = await apiFor(event).PUT("/api/v1/leave/rate/{user_id}", {
      params: { path: { user_id: userId } },
      body: { hourly_rate },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { rateSaved: true };
  },

  /** A new employment contract (#65). A *changed* contract is a new row, never an in-place edit. */
  saveContract: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const start = String(form.get("start_date") ?? "");
    const hoursRaw = String(form.get("contract_hours_per_week") ?? "")
      .trim()
      .replace(",", ".");
    if (!userId || !start || !hoursRaw) return fail(400, { error: "errors.required" });
    const endRaw = String(form.get("end_date") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/leave/contracts", {
      body: {
        user_id: userId,
        start_date: start,
        end_date: endRaw || null,
        contract_hours_per_week: hoursRaw,
        note: String(form.get("note") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { contractSaved: true };
  },

  /** Terminate a contract by setting its end date (the row survives — it's history). */
  terminateContract: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("contract_id") ?? "");
    const end = String(form.get("end_date") ?? "");
    if (!id || !end) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/leave/contracts/{contract_id}", {
      params: { path: { contract_id: id } },
      body: { end_date: end },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { contractSaved: true };
  },

  deleteContract: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("contract_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/leave/contracts/{contract_id}", {
        params: { path: { contract_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { contractSaved: true };
  },

  /** A recurring rostered-free-day pattern (#107): saved, and its days placed right away. */
  saveRecurring: async (event) => {
    const form = await event.request.formData();
    const userId = String(form.get("user_id") ?? "");
    const typeId = String(form.get("leave_type_id") ?? "");
    const anchor = String(form.get("anchor_date") ?? "");
    const interval = Number(form.get("interval_weeks") ?? 0);
    if (!userId || !typeId || !anchor || !interval) {
      return fail(400, { error: "errors.required" });
    }
    const { data, error } = await apiFor(event).POST("/api/v1/leave/recurring", {
      body: {
        user_id: userId,
        leave_type_id: typeId,
        anchor_date: anchor,
        interval_weeks: interval,
        // Part-day pattern ("off from 15:00") — absent fields mean the whole scheduled day.
        start_time: String(form.get("start_time") ?? "").trim() || null,
        end_time: String(form.get("end_time") ?? "").trim() || null,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { recurringSaved: true, recurringGenerated: data?.generated ?? 0 };
  },

  /** Deactivating stops future generation; the days already placed stay. */
  toggleRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).PATCH("/api/v1/leave/recurring/{recurring_id}", {
      params: { path: { recurring_id: id } },
      body: { active: form.get("active") === "true" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { recurringSaved: true, recurringGenerated: data?.generated ?? 0 };
  },

  deleteRecurring: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/leave/recurring/{recurring_id}", {
        params: { path: { recurring_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { recurringSaved: true, recurringGenerated: 0 };
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
