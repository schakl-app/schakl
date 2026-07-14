import { fail } from "@sveltejs/kit";

import {
  aggregateEventsByDay,
  isCalendarView,
  rangeFor,
  type CalendarView,
} from "$lib/core/calendar";
import { calendarSourcesFor, type CalendarEvent, type CalendarPerson } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import {
  createScheduleAction,
  deleteScheduleAction,
  logScheduleTimeAction,
  updateScheduleAction,
} from "$lib/modules/tasks/schedule-actions.server";

import type { Actions, PageServerLoad } from "./$types";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function isIsoDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

/**
 * The shared calendar (core surface, like the dashboard): composes event feeds contributed
 * by enabled modules via the registry — the team's leave, task blocks + deadlines (#188), and
 * Google Calendar. One parallel fan of source loads; a failing source degrades to an empty feed.
 *
 * `?view=` + `?date=` win over the stored pref (from the layout load) when present, so a
 * shared link is authoritative and back/forward navigate correctly. The year view never
 * ships raw events to the client — only per-day aggregates (docs/PERFORMANCE.md).
 */
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const today = todayIso();
  const { defaultView, hiddenSources, peopleBySource } = await event.parent();

  const rawDate = event.url.searchParams.get("date") ?? "";
  const date = isIsoDate(rawDate) ? rawDate : today;

  const rawView = event.url.searchParams.get("view");
  const view: CalendarView = isCalendarView(rawView) ? rawView : defaultView;

  // The viewer rides along so a source can mark its events as own/draggable (#106) and decide
  // which colleagues it may overlay (#188) — UX hints only; every move is re-checked by the API.
  const baseRange = {
    ...rangeFor(view, date),
    locale: event.locals.locale,
    user: event.locals.user,
  };
  const allSources = calendarSourcesFor(event.locals.theme?.enabledModules ?? []);
  // The visibility menu doubles as the legend (#121), so it lists every enabled feed —
  // hidden ones included — while only the visible ones are loaded: a hidden feed costs
  // no API call (docs/PERFORMANCE.md).
  const hidden = new Set(hiddenSources);
  const sources = allSources.filter((source) => !hidden.has(source.key));
  // A per-source range carries that source's own colleague overlay (#188).
  const results = await Promise.all(
    sources.map((source) =>
      source
        .load(api, { ...baseRange, people: peopleBySource[source.key] ?? [] })
        .catch(() => [] as CalendarEvent[]),
    ),
  );
  const events = results.flat();

  // Rosters for the per-person feed menu (#188): only for sources that offer one, only when
  // visible, and only if the viewer may overlay anyone (the source returns [] otherwise).
  const rosters = await Promise.all(
    allSources.map((source) =>
      !hidden.has(source.key) && source.people
        ? source.people(api, baseRange).catch(() => [] as CalendarPerson[])
        : Promise.resolve([] as CalendarPerson[]),
    ),
  );

  const sourceOptions = allSources.map((source, index) => ({
    key: source.key,
    labelKey: source.labelKey,
    color: source.color,
    hidden: hidden.has(source.key),
    people: rosters[index],
    selectedPeople: peopleBySource[source.key] ?? [],
  }));

  const base = { view, date, today, sourceOptions };
  if (view === "year") {
    return { ...base, events: [], aggregates: aggregateEventsByDay(events) };
  }
  return { ...base, events, aggregates: null };
};

export const actions: Actions = {
  /** The feeds this user hid (#121) — the whole list per save, like every roster post. */
  saveSources: async (event) => {
    const form = await event.request.formData();
    const hiddenSources = form.getAll("hidden").map(String).filter(Boolean);
    await apiFor(event).PUT("/api/v1/prefs", {
      body: { prefs: { calendar: { hiddenSources } } },
    });
    return { sourcesSaved: true };
  },

  /**
   * The colleagues this user overlays for one source (#188) — the whole selection for that
   * source key per save, merged into the `calendar.people` map so other sources keep theirs.
   */
  savePeople: async (event) => {
    const form = await event.request.formData();
    const sourceKey = String(form.get("source") ?? "");
    if (!sourceKey) return fail(400, { error: "errors.required" });
    const ids = form.getAll("person").map(String).filter(Boolean);
    const prefs = await apiFor(event).GET("/api/v1/prefs");
    const people =
      (prefs.data?.prefs as { calendar?: { people?: Record<string, string[]> } } | undefined)
        ?.calendar?.people ?? {};
    await apiFor(event).PUT("/api/v1/prefs", {
      body: { prefs: { calendar: { people: { ...people, [sourceKey]: ids } } } },
    });
    return { peopleSaved: true };
  },

  // Personal "last used view" preference (saved per user, never in org Settings).
  saveView: async (event) => {
    const form = await event.request.formData();
    const view = String(form.get("view") ?? "");
    if (isCalendarView(view)) {
      await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { calendar: { view } } } });
    }
    return { viewSaved: true };
  },

  /**
   * Drag-to-reschedule (#106): dispatch a dropped chip back to the module that owns it. The
   * core calendar knows no module's API — the source registered a `move` alongside its `load`,
   * and the API behind it recomputes hours and re-triggers approval (CLAUDE.md §14, #72).
   */
  moveEvent: async (event) => {
    const form = await event.request.formData();
    const sourceKey = String(form.get("source") ?? "");
    const id = String(form.get("id") ?? "");
    const deltaDays = Number(form.get("delta") ?? 0);
    if (!sourceKey || !id || !Number.isInteger(deltaDays)) {
      return fail(400, { error: "errors.required" });
    }
    if (deltaDays === 0) return { moved: false };
    const source = calendarSourcesFor(event.locals.theme?.enabledModules ?? []).find(
      (s) => s.key === sourceKey,
    );
    if (!source?.move) return fail(400, { error: "errors.not_found" });
    const error = await source.move(apiFor(event), { id, deltaDays });
    if (error) return fail(400, { error });
    return { moved: true };
  },

  // Task scheduling from the calendar "+" (#188). The shared modal posts here; the same
  // helpers back the task detail page's actions so the two can't drift.
  scheduleTask: async (event) => {
    const result = await createScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduled: true };
  },
  updateSchedule: async (event) => {
    const result = await updateScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduleUpdated: true };
  },
  deleteSchedule: async (event) => {
    const result = await deleteScheduleAction(event);
    return result.error ? fail(400, { error: result.error }) : { scheduleDeleted: true };
  },
  logScheduleTime: async (event) => {
    const result = await logScheduleTimeAction(event);
    return result.error ? fail(400, { error: result.error }) : { timeLogged: true };
  },
};
