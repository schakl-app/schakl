import { fail } from "@sveltejs/kit";

import {
  aggregateEventsByDay,
  isCalendarView,
  rangeFor,
  type CalendarView,
} from "$lib/core/calendar";
import { calendarSourcesFor, type CalendarEvent, type CalendarPerson } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import { isHexColor, LABEL_COLORS } from "$lib/core/ui/colors";
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
/** The namespaced prefs key for one colleague's colour/hide on a split feed (#281). */
function personKey(sourceKey: string, userId: string): string {
  return `${sourceKey}:person:${userId}`;
}

/** Pull a source's per-colleague colour overrides out of the flat `calendar.colors` map (#281). */
function personColorsFor(
  sourceKey: string,
  colors: Record<string, string>,
): Record<string, string> {
  const prefix = `${sourceKey}:person:`;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(colors)) {
    if (key.startsWith(prefix)) out[key.slice(prefix.length)] = value;
  }
  return out;
}

/** The colleagues this viewer hid from a split feed (#281), from the namespaced hidden keys. */
function hiddenPeopleFor(sourceKey: string, hidden: Set<string>): string[] {
  const prefix = `${sourceKey}:person:`;
  return [...hidden].filter((k) => k.startsWith(prefix)).map((k) => k.slice(prefix.length));
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const today = todayIso();
  const { defaultView, hiddenSources, peopleBySource, colors } = await event.parent();

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
  // no API call (docs/PERFORMANCE.md). `hidden` also holds namespaced per-colleague hides (#281);
  // those never match a top-level `source.key`, so this filter ignores them (the source drops
  // the hidden colleague's items itself, from `hiddenPeople`).
  const hidden = new Set(hiddenSources);
  const sources = allSources.filter((source) => !hidden.has(source.key));
  // A per-source range carries that source's own colleague overlay (#188) and the viewer's
  // personal colour overrides / per-colleague hides (#281).
  const results = await Promise.all(
    sources.map((source) =>
      source
        .load(api, {
          ...baseRange,
          people: peopleBySource[source.key] ?? [],
          color: colors[source.key],
          personColors: personColorsFor(source.key, colors),
          hiddenPeople: hiddenPeopleFor(source.key, hidden),
        })
        .catch(() => [] as CalendarEvent[]),
    ),
  );
  const events = results.flat();

  // Rosters for the per-person feed menu: the overlay picker (#188) and the split-by-colleague
  // rows (#281). Both only for visible sources that offer them, and only if the viewer may see
  // anyone else (the source returns [] otherwise).
  const [rosters, splitRosters] = await Promise.all([
    Promise.all(
      allSources.map((source) =>
        !hidden.has(source.key) && source.people
          ? source.people(api, baseRange).catch(() => [] as CalendarPerson[])
          : Promise.resolve([] as CalendarPerson[]),
      ),
    ),
    Promise.all(
      allSources.map((source) =>
        !hidden.has(source.key) && source.splitPeople
          ? source.splitPeople(api, baseRange).catch(() => [] as CalendarPerson[])
          : Promise.resolve([] as CalendarPerson[]),
      ),
    ),
  ]);

  const sourceOptions = allSources.map((source, index) => ({
    key: source.key,
    labelKey: source.labelKey,
    // Effective legend swatch: the viewer's override, else the module default (#281).
    color: colors[source.key] ?? source.color,
    defaultColor: source.color,
    colorable: source.colorable !== false,
    hidden: hidden.has(source.key),
    people: rosters[index],
    selectedPeople: peopleBySource[source.key] ?? [],
    // Split-by-colleague rows (#281): each with its own override (empty = auto, inherits the
    // feed colour / leave-type colour) and its own hidden flag.
    splitPeople: splitRosters[index].map((person) => ({
      id: person.id,
      name: person.name,
      color: colors[personKey(source.key, person.id)] ?? "",
      hidden: hidden.has(personKey(source.key, person.id)),
    })),
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

  /**
   * The viewer's personal feed / per-colleague colours (#281) — the whole `calendar.colors` map
   * per save, like `saveSources`. Each value must be a known token or a `#hex`; anything else is
   * dropped, so a hand-crafted post can never wedge arbitrary text into a chip's inline style.
   */
  saveColors: async (event) => {
    const form = await event.request.formData();
    let parsed: unknown;
    try {
      parsed = JSON.parse(String(form.get("colors") ?? "{}"));
    } catch {
      return fail(400, { error: "errors.required" });
    }
    const tokens = new Set<string>(LABEL_COLORS);
    const colors: Record<string, string> = {};
    if (parsed && typeof parsed === "object") {
      for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof value === "string" && (tokens.has(value) || isHexColor(value))) {
          colors[key] = value;
        }
      }
    }
    await apiFor(event).PUT("/api/v1/prefs", { body: { prefs: { calendar: { colors } } } });
    return { colorsSaved: true };
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
