import { isCalendarView, type CalendarView } from "$lib/core/calendar";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Personal "last used view" (day/week/month/year), URL-independent so it doesn't refetch on
 * every prev/next/switcher click — mirrors `(app)/time/+layout.server.ts`.
 */
export const load: LayoutServerLoad = async (event) => {
  const prefs = await apiFor(event).GET("/api/v1/prefs");
  const calendar = (
    prefs.data?.prefs as { calendar?: { view?: string; hiddenSources?: unknown } } | undefined
  )?.calendar;
  const storedView = calendar?.view;
  const defaultView: CalendarView = isCalendarView(storedView) ? storedView : "week";
  // Which feeds this user hid (#121). Rides the same prefs read — no extra call.
  const storedHidden = calendar?.hiddenSources;
  const hiddenSources = Array.isArray(storedHidden)
    ? storedHidden.filter((s): s is string => typeof s === "string")
    : [];
  return { defaultView, hiddenSources };
};
