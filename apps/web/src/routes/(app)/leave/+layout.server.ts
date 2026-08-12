import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { availabilityWindow } from "$lib/modules/leave/availability.server";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by every leave page (type labels/colors, own contract hours and the average
 * scheduled working day the "≈ n dagen" hints divide by). A layout load that never touches the
 * URL, so year/tab navigation doesn't refetch them.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [types, profile, recurring, availability] = await Promise.all([
    api.GET("/api/v1/leave/types"),
    api.GET("/api/v1/leave/profile"),
    // Own recurring free days (#107) — self-service surface on Mijn verlof. Explicitly own:
    // a manager's unfiltered list would be the whole org's.
    event.locals.user
      ? api.GET("/api/v1/leave/recurring", {
          params: { query: { user_id: event.locals.user.id } },
        })
      : Promise.resolve({ data: null }),
    // Own availability exceptions — the freelancer's self-service surface. Permission-gated so
    // a role without it pays for no call at all rather than for one that 403s.
    event.locals.user && can(event.locals.user, "leave.availability.read")
      ? api.GET("/api/v1/leave/availability", {
          params: { query: { ...availabilityWindow(), user_id: event.locals.user.id } },
        })
      : Promise.resolve({ data: null }),
  ]);
  return {
    leaveTypes: types.data ?? [],
    hoursPerWeek: Number(profile.data?.hours_per_week ?? 40),
    hoursPerDay: Number(profile.data?.hours_per_day ?? 8),
    // The kind of the period in force — what decides whether the availability section exists at
    // all. `null` (no period on file) is not `employee`: a tenant with no contracts shows it to
    // nobody rather than to everybody.
    employmentType: profile.data?.employment_type ?? null,
    myRecurring: recurring.data ?? [],
    // `null` when unreadable, `[]` when there simply are none — the two render differently.
    myAvailability: availability.data ?? null,
    locale: event.locals.locale,
  };
};
