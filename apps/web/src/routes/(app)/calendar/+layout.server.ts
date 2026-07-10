import { isCalendarView, type CalendarView } from "$lib/core/calendar";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * Personal "last used view" (day/week/month/year), URL-independent so it doesn't refetch on
 * every prev/next/switcher click — mirrors `(app)/time/+layout.server.ts`.
 */
export const load: LayoutServerLoad = async (event) => {
  const prefs = await apiFor(event).GET("/api/v1/prefs");
  const stored = (prefs.data?.prefs as { calendar?: { view?: string } } | undefined)?.calendar
    ?.view;
  const defaultView: CalendarView = isCalendarView(stored) ? stored : "week";
  return { defaultView };
};
