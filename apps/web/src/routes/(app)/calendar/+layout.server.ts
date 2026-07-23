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
    prefs.data?.prefs as
      | {
          calendar?: {
            view?: string;
            hiddenSources?: unknown;
            people?: unknown;
            colors?: unknown;
          };
        }
      | undefined
  )?.calendar;
  const storedView = calendar?.view;
  const defaultView: CalendarView = isCalendarView(storedView) ? storedView : "week";
  // Which feeds this user hid (#121). Rides the same prefs read — no extra call. The list holds
  // both top-level source keys and, since #281, namespaced per-colleague hides
  // (`<sourceKey>:person:<userId>`); the page load splits the latter out per source.
  const storedHidden = calendar?.hiddenSources;
  const hiddenSources = Array.isArray(storedHidden)
    ? storedHidden.filter((s): s is string => typeof s === "string")
    : [];
  // Per-source colleague overlays (#188): `{ "<sourceKey>": ["<userId>", …] }`. Same prefs read.
  const rawPeople = calendar?.people;
  const peopleBySource: Record<string, string[]> = {};
  if (rawPeople && typeof rawPeople === "object") {
    for (const [key, ids] of Object.entries(rawPeople as Record<string, unknown>)) {
      if (Array.isArray(ids))
        peopleBySource[key] = ids.filter((v): v is string => typeof v === "string");
    }
  }
  // Personal colour overrides (#281): a flat `{ "<sourceKey>"|"<sourceKey>:person:<userId>":
  // "<token|#hex>" }`. Same prefs read; the page load splits it per source.
  const rawColors = calendar?.colors;
  const colors: Record<string, string> = {};
  if (rawColors && typeof rawColors === "object") {
    for (const [key, value] of Object.entries(rawColors as Record<string, unknown>)) {
      if (typeof value === "string" && value) colors[key] = value;
    }
  }
  return { defaultView, hiddenSources, peopleBySource, colors };
};
