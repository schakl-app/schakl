import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Lookups shared by every leave page (type labels/colors, own contract hours and the average
 * scheduled working day the "≈ n dagen" hints divide by). A layout load that never touches the
 * URL, so year/tab navigation doesn't refetch them.
 */
export const load: LayoutServerLoad = async (event) => {
  const api = apiFor(event);
  const [types, profile] = await Promise.all([
    api.GET("/api/v1/leave/types"),
    api.GET("/api/v1/leave/profile"),
  ]);
  return {
    leaveTypes: types.data ?? [],
    hoursPerWeek: Number(profile.data?.hours_per_week ?? 40),
    hoursPerDay: Number(profile.data?.hours_per_day ?? 8),
    locale: event.locals.locale,
  };
};
