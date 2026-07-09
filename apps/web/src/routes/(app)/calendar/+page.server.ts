import {
  aggregateEventsByDay,
  isCalendarView,
  rangeFor,
  type CalendarView,
} from "$lib/core/calendar";
import { calendarSourcesFor, type CalendarEvent } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function isIsoDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

/**
 * The shared calendar (core surface, like the dashboard): composes event feeds contributed
 * by enabled modules via the registry — today the team's leave; Google Calendar joins in P3.
 * One parallel fan of source loads; a failing source degrades to an empty feed.
 *
 * `?view=` + `?date=` win over the stored pref (from the layout load) when present, so a
 * shared link is authoritative and back/forward navigate correctly. The year view never
 * ships raw events to the client — only per-day aggregates (docs/PERFORMANCE.md).
 */
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const today = todayIso();
  const { defaultView } = await event.parent();

  const rawDate = event.url.searchParams.get("date") ?? "";
  const date = isIsoDate(rawDate) ? rawDate : today;

  const rawView = event.url.searchParams.get("view");
  const view: CalendarView = isCalendarView(rawView) ? rawView : defaultView;

  const range = { ...rangeFor(view, date), locale: event.locals.locale };
  const sources = calendarSourcesFor(event.locals.theme?.enabledModules ?? []);
  const results = await Promise.all(
    sources.map((source) => source.load(api, range).catch(() => [] as CalendarEvent[])),
  );
  const events = results.flat();

  if (view === "year") {
    return { view, date, today, events: [], aggregates: aggregateEventsByDay(events) };
  }
  return { view, date, today, events, aggregates: null };
};

export const actions: Actions = {
  // Personal "last used view" preference (saved per user, never in org Settings).
  saveView: async (event) => {
    const form = await event.request.formData();
    const view = String(form.get("view") ?? "");
    if (isCalendarView(view)) {
      await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { calendar: { view } } } });
    }
    return { viewSaved: true };
  },
};
