/**
 * How this app reads a span of time. A duration is one of the few things an agency says out
 * loud all day, so every field whose subject is one takes dictation: "1:30", "90", "90m",
 * "1h30", "1,5" and "1.5" all mean the same ninety minutes, and what is stored is always an
 * integer number of them.
 *
 * It lives in core rather than in `modules/time/` because the time module is no longer the only
 * one that needs it (§6: no cross-module internals) — a task's budget, a scheduled block and a
 * break are durations too.
 */

/**
 * Forgiving duration parser → minutes, or null when unreadable.
 * Accepts "1:30", "2h", "2h15", "2h 15m", "90m", "90", "1,5" and "1.5" (decimal hours).
 */
export function parseDurationText(raw: string): number | null {
  const text = raw.trim().toLowerCase().replace(",", ".");
  if (!text) return null;

  const colon = /^(\d{1,2}):(\d{2})$/.exec(text);
  if (colon) return Number(colon[1]) * 60 + Number(colon[2]);

  const hoursMinutes = /^(\d{1,2})\s*h(?:\s*(\d{1,2})\s*m?)?$/.exec(text);
  if (hoursMinutes) return Number(hoursMinutes[1]) * 60 + Number(hoursMinutes[2] ?? 0);

  const minutesOnly = /^(\d{1,5})\s*m$/.exec(text);
  if (minutesOnly) return Number(minutesOnly[1]);

  // Five digits, not two: "100" is an ordinary hour and forty and used to fall through to null,
  // so the one shape the old stepped input demanded was the one this parser refused.
  const decimal = /^(\d{1,5}(?:\.\d+)?)$/.exec(text);
  if (decimal) {
    const value = Number(decimal[1]);
    // A bare integer ≥ 5 reads as minutes ("90" → 90m); small/decimal values as hours.
    if (Number.isInteger(value) && value >= 5) return value;
    return Math.round(value * 60);
  }
  return null;
}

/** Minutes → the canonical text shown in the duration input ("1:30"). */
export function formatDurationInput(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

/**
 * A posted duration → integer minutes, or null when absent, empty or unreadable.
 *
 * `DurationInput` posts the text the user typed (canonicalised to "1:30" when JS is on), so the
 * server runs the same parser the browser does and a JS-off post, a hand-rolled request and a
 * bare number all land on the same value. The numeric fallback is what keeps an integer sent by
 * an API client working — including one wider than the parser's own digit cap.
 */
export function parsePostedMinutes(raw: unknown): number | null {
  if (raw == null) return null;
  const text = String(raw).trim();
  if (!text) return null;
  const parsed = parseDurationText(text);
  if (parsed != null) return parsed > 0 ? Math.round(parsed) : null;
  const numeric = Number(text);
  return Number.isFinite(numeric) && numeric > 0 ? Math.round(numeric) : null;
}
