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
 * The field is **empty until the form has hydrated**: the server renders no payload at all, so a
 * submit that lands before the JavaScript is attached — a slow connection, a stale chunk after a
 * deploy, a click in the first second — posts `""`, and the action refuses it
 * (`errors.form_not_ready`) instead of writing the server-rendered snapshot and reporting
 * success over changes that were never sent. `parseMatrixPayload("")` is therefore `null` on
 * purpose, and the action tells the two apart before calling it.
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

/** One event's e-mail override (#245). The digest schedule is global, so no time/weekday here. */
export interface MatrixEmailEventWrite {
  event_type: string;
  enabled: boolean;
  delay_minutes: number;
  digest: string;
}

/** One event routed to one external channel (#283, #295). Absent = not routed. */
export interface MatrixChannelEventWrite {
  event_type: string;
  enabled: boolean;
  delay_minutes: number;
  digest: string;
}

export interface MatrixChannelWrite {
  channel_config_id: string;
  events: MatrixChannelEventWrite[];
}

export interface MatrixWrite {
  events: MatrixEventWrite[];
  email_events: MatrixEmailEventWrite[];
  /** One event's browser-push override (#309). Same three fields as e-mail's. */
  push_events: MatrixEmailEventWrite[];
  general: {
    due_soon_days: number | null;
    quiet_hours_start: string | null;
    quiet_hours_end: string | null;
  } | null;
  /** The scope's global e-mail digest schedule; null = inherit. */
  email: {
    digest_time: string | null;
    digest_weekday: number | null;
  } | null;
  /** The scope's global browser-push digest schedule (#309); null = inherit. */
  push: {
    digest_time: string | null;
    digest_weekday: number | null;
  } | null;
  /** This scope's external channels, each with its per-event routing (#283, #295). */
  channels: MatrixChannelWrite[];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * "HH:MM" as the pickers post it, or the API's own "HH:MM:SS" — an in-app row re-posts the
 * `digest_time` it was loaded with, and reading only the short form dropped every stored time
 * on the first save. Normalised to "HH:MM"; the API accepts either.
 */
function asTime(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const m = /^(\d{2}:\d{2})(?::\d{2})?$/.exec(value);
  return m ? m[1] : null;
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

  /** E-mail and browser push post the same three fields, so they decode the same way (#309). */
  function pushedEvents(raw: unknown): MatrixEmailEventWrite[] {
    const rows: MatrixEmailEventWrite[] = [];
    if (!Array.isArray(raw)) return rows;
    for (const entry of raw) {
      if (!isPlainObject(entry) || typeof entry.event_type !== "string") continue;
      const delay = Number(entry.delay_minutes);
      rows.push({
        event_type: entry.event_type,
        enabled: entry.enabled === true,
        delay_minutes: Number.isFinite(delay) && delay >= 0 ? Math.trunc(delay) : 0,
        digest: typeof entry.digest === "string" ? entry.digest : "immediate",
      });
    }
    return rows;
  }

  const emailEvents = pushedEvents(parsed.email_events);
  const pushEvents = pushedEvents(parsed.push_events);

  let general: MatrixWrite["general"] = null;
  if (isPlainObject(parsed.general)) {
    const days = Number(parsed.general.due_soon_days);
    general = {
      due_soon_days: Number.isFinite(days) && days >= 0 ? Math.trunc(days) : null,
      quiet_hours_start: asTime(parsed.general.quiet_hours_start),
      quiet_hours_end: asTime(parsed.general.quiet_hours_end),
    };
  }

  /** A scope's digest schedule block; the two implicit pushed channels each have one. */
  function schedule(raw: unknown): MatrixWrite["email"] {
    if (!isPlainObject(raw)) return null;
    const weekday = Number(raw.digest_weekday);
    return {
      digest_time: asTime(raw.digest_time),
      digest_weekday: Number.isInteger(weekday) && weekday >= 0 && weekday <= 6 ? weekday : null,
    };
  }

  const email = schedule(parsed.email);
  const push = schedule(parsed.push);

  const channels: MatrixChannelWrite[] = [];
  if (Array.isArray(parsed.channels)) {
    for (const block of parsed.channels) {
      if (!isPlainObject(block) || typeof block.channel_config_id !== "string") continue;
      const rows: MatrixChannelEventWrite[] = [];
      if (Array.isArray(block.events)) {
        for (const entry of block.events) {
          if (!isPlainObject(entry) || typeof entry.event_type !== "string") continue;
          const delay = Number(entry.delay_minutes);
          rows.push({
            event_type: entry.event_type,
            enabled: entry.enabled === true,
            delay_minutes: Number.isFinite(delay) && delay >= 0 ? Math.trunc(delay) : 0,
            digest: typeof entry.digest === "string" ? entry.digest : "immediate",
          });
        }
      }
      channels.push({ channel_config_id: block.channel_config_id, events: rows });
    }
  }

  return {
    events,
    email_events: emailEvents,
    push_events: pushEvents,
    general,
    email,
    push,
    channels,
  };
}
