/**
 * Decode the preference matrix a `?/save` form action posts (issue #16).
 *
 * The browser sends JSON in a hidden field because only it knows which rows the user *changed* —
 * and posting every row would turn the whole matrix into overrides, which is precisely what the
 * three-layer resolution exists to avoid.
 *
 * Everything here treats the payload as hostile: it arrives from a form, so a malformed blob must
 * degrade to "nothing to save" rather than a 500. The API validates the vocabulary (event names,
 * digest cadences) itself and answers with the standard envelope, so this only shapes the JSON.
 *
 * `.server.ts`: never bundled to the browser.
 */

export interface MatrixEventWrite {
  event_type: string;
  enabled: boolean;
  delay_minutes: number;
  digest: string;
  digest_time: string | null;
  digest_weekday: number | null;
}

export interface MatrixWrite {
  events: MatrixEventWrite[];
  general: {
    due_soon_days: number | null;
    quiet_hours_start: string | null;
    quiet_hours_end: string | null;
  } | null;
}

/**
 * What the screen renders if the API somehow answers with nothing. A matrix with no rows renders
 * an empty table, which is honest; a `null` general block would render nothing at all.
 * `due_soon_days` mirrors `notifications/defaults.py::DEFAULT_DUE_SOON_DAYS`.
 */
export const EMPTY_MATRIX: {
  events: never[];
  general: {
    due_soon_days: number;
    quiet_hours_start: string | null;
    quiet_hours_end: string | null;
    source: string;
  };
} = {
  events: [],
  general: {
    due_soon_days: 3,
    quiet_hours_start: null,
    quiet_hours_end: null,
    source: "default",
  },
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asTime(value: unknown): string | null {
  return typeof value === "string" && /^\d{2}:\d{2}$/.test(value) ? value : null;
}

export function parseMatrixPayload(raw: FormDataEntryValue | null): MatrixWrite | null {
  if (typeof raw !== "string") return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isPlainObject(parsed) || !Array.isArray(parsed.events)) return null;

  const events: MatrixEventWrite[] = [];
  for (const entry of parsed.events) {
    if (!isPlainObject(entry) || typeof entry.event_type !== "string") continue;
    const delay = Number(entry.delay_minutes);
    const weekday = Number(entry.digest_weekday);
    events.push({
      event_type: entry.event_type,
      enabled: entry.enabled !== false,
      delay_minutes: Number.isFinite(delay) && delay >= 0 ? Math.trunc(delay) : 0,
      digest: typeof entry.digest === "string" ? entry.digest : "immediate",
      digest_time: asTime(entry.digest_time),
      digest_weekday: Number.isInteger(weekday) && weekday >= 0 && weekday <= 6 ? weekday : null,
    });
  }

  let general: MatrixWrite["general"] = null;
  if (isPlainObject(parsed.general)) {
    const days = Number(parsed.general.due_soon_days);
    general = {
      due_soon_days: Number.isFinite(days) && days >= 0 ? Math.trunc(days) : null,
      quiet_hours_start: asTime(parsed.general.quiet_hours_start),
      quiet_hours_end: asTime(parsed.general.quiet_hours_end),
    };
  }

  return { events, general };
}
