import { monthGrid, monthOf } from "$lib/core/calendar";
import { calendarSourcesFor, type CalendarEvent } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * The shared calendar (core surface, like the dashboard): composes event feeds contributed
 * by enabled modules via the registry — today the team's leave; Google Calendar joins in P3.
 * One parallel fan of source loads; a failing source degrades to an empty feed.
 */
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const today = todayIso();
  const raw = event.url.searchParams.get("month") ?? "";
  const month = /^\d{4}-(0[1-9]|1[0-2])$/.test(raw) ? raw : monthOf(today);

  // The grid shows full weeks, so fetch the whole visible range, not just the month.
  const days = monthGrid(month);
  const range = { from: days[0], to: days[days.length - 1], locale: event.locals.locale };

  const sources = calendarSourcesFor(event.locals.theme?.enabledModules ?? []);
  const results = await Promise.all(
    sources.map((source) => source.load(api, range).catch(() => [] as CalendarEvent[])),
  );

  return { month, today, events: results.flat() };
};
