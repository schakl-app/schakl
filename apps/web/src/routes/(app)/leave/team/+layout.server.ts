import { can } from "$lib/core/permissions";
import { defaultSchedule } from "$lib/modules/leave/schedule";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The roster's URL-independent reads (#290): the member names, and — for someone who may manage
 * employment data — the schedules, contracts, recurring free days and the org's default week.
 * None of them varies with the year switcher or the sort, which are the two things this page's
 * load exists to react to, so switching year used to refetch all five for identical answers.
 *
 * Its own layout rather than the shared `leave/` one: `/leave` is every employee's personal page
 * and holds none of these permissions, so widening that layout would put five approver-only
 * calls (four of which 403) on every visit to it.
 *
 * `manage`-gated exactly as the page was: an approver who cannot manage schedules gets no ⋯ menu
 * and pays for none of these calls. `profiles` stays `null` rather than `[]` when unreadable —
 * "not permitted" and "nobody has one" render differently.
 *
 * No `await event.parent()` — nothing here depends on the leave or app layouts.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  // A non-approver is redirected by the page load; this keeps the *shape* constant either way,
  // so every consumer stays non-optional rather than guarding a case that cannot render.
  const approver = can(event.locals.user, "leave.request.approve");
  const manage = approver && can(event.locals.user, "leave.profile.manage");
  const [members, profiles, contracts, recurring, settings] = await Promise.all([
    approver ? api.GET("/api/v1/members/lookup") : Promise.resolve({ data: null }),
    manage ? api.GET("/api/v1/leave/profiles") : Promise.resolve({ data: null }),
    manage
      ? api.GET("/api/v1/leave/contracts", { params: { query: { all_users: true } } })
      : Promise.resolve({ data: null }),
    manage ? api.GET("/api/v1/leave/recurring") : Promise.resolve({ data: null }),
    manage ? api.GET("/api/v1/leave/settings") : Promise.resolve({ data: null }),
  ]);
  return {
    members: members.data ?? [],
    profiles: profiles.data ?? null,
    manageEmployment: manage,
    contracts: contracts.data ?? [],
    recurring: recurring.data ?? [],
    defaultSchedule: settings.data?.default_schedule ?? defaultSchedule(),
  };
};
