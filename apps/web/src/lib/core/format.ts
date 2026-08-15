/**
 * Locale-aware date/number formatting (CLAUDE.md §8) with a European preference:
 * the UI locale maps to a European Intl locale (en → en-GB), so English users still get
 * day-month ordering and 24-hour clocks. Date-only ISO strings are wall-clock values and
 * are formatted in UTC so they never shift a day; an instant is formatted in the tenant's zone.
 *
 * Which of the two a value is comes from the value (`wire-date.ts`), not from which function was
 * called — a date formatter handed a timestamp used to print `NaN-NaN-0NaN`.
 */
import { getCurrency } from "$lib/core/currency";
import { getClock, getDateFormat } from "$lib/core/dateformat";
import { getTimeZone } from "$lib/core/timezone";
import { numericDate, parseWireDate, wireZone } from "$lib/core/wire-date";
import { getLocale } from "$lib/paraglide/runtime";

const INTL_LOCALE: Record<string, string> = {
  nl: "nl-NL",
  en: "en-GB",
};

export function dateLocale(): string {
  return INTL_LOCALE[getLocale()] ?? "en-GB";
}

const _cache = new Map<string, Intl.DateTimeFormat>();

function fmt(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const locale = dateLocale();
  const key = locale + JSON.stringify(options);
  let formatter = _cache.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, options);
    _cache.set(key, formatter);
  }
  return formatter;
}

/**
 * The zone this value's calendar date is read in — UTC for a wall-clock date, the tenant's for an
 * instant. Every date formatter below asks, so any of them may be handed either shape.
 */
function zone(iso: string): string {
  return wireZone(iso, getTimeZone());
}

/** "7 jul" — for due dates, list rows, chips. Takes a date-only ISO string. */
export function fmtDayMonth(isoDate: string): string {
  return fmt({ day: "numeric", month: "short", timeZone: zone(isoDate) }).format(
    parseWireDate(isoDate),
  );
}

/** "7 jul 2027" — day-month with its year, for dates outside the current year. */
export function fmtDayMonthYear(isoDate: string): string {
  return fmt({ day: "numeric", month: "short", year: "numeric", timeZone: zone(isoDate) }).format(
    parseWireDate(isoDate),
  );
}

/**
 * The character between the two ends of a range — a plain hyphen, everywhere, on purpose.
 *
 * Typography says an en dash; the owner's instruction says a hyphen, and a date range is read
 * far more often than it is admired. It lives here as one constant so the answer is the same on
 * every screen: a range assembled by hand in some component is how "1 jul - 31 jul" and
 * "1 jul – 31 jul" ended up on the same page.
 */
export const RANGE_DASH = "-";

/**
 * A date-only period (#119): "3 dec" / "3 dec - 7 dec" while it lies in the current org-local
 * calendar year; the year appears when it doesn't ("3 dec 2027", "3 dec - 7 dec 2027") and on
 * both ends when the span crosses a year boundary ("28 dec 2026 - 3 jan 2027"). Omitting `end`
 * formats a single date year-aware.
 */
export function fmtPeriod(startIso: string, endIso: string = startIso): string {
  const startYear = startIso.slice(0, 4);
  const endYear = endIso.slice(0, 4);
  const currentYear = fmt({ year: "numeric", timeZone: getTimeZone() }).format(new Date());
  if (startYear !== endYear)
    return `${fmtDayMonthYear(startIso)} ${RANGE_DASH} ${fmtDayMonthYear(endIso)}`;
  if (startIso === endIso)
    return startYear === currentYear ? fmtDayMonth(startIso) : fmtDayMonthYear(startIso);
  const end = startYear === currentYear ? fmtDayMonth(endIso) : fmtDayMonthYear(endIso);
  return `${fmtDayMonth(startIso)} ${RANGE_DASH} ${end}`;
}

/** "ma 7" — weekday + day, for grid column headers. Date-only ISO string. */
export function fmtWeekdayDay(isoDate: string): string {
  return fmt({ weekday: "short", day: "numeric", timeZone: zone(isoDate) }).format(
    parseWireDate(isoDate),
  );
}

/** "ma" — weekday abbreviation. Date-only ISO string. */
export function fmtWeekdayShort(isoDate: string): string {
  return fmt({ weekday: "short", timeZone: zone(isoDate) }).format(parseWireDate(isoDate));
}

/** "maandag 7 juli" — the day heading. Date-only ISO string. */
export function fmtLongDay(isoDate: string): string {
  return fmt({ weekday: "long", day: "numeric", month: "long", timeZone: zone(isoDate) }).format(
    parseWireDate(isoDate),
  );
}

/** "juli 2026" — calendar popover heading. Takes a "yyyy-mm" month. */
export function fmtMonthYear(month: string): string {
  return fmt({ month: "long", year: "numeric", timeZone: "UTC" }).format(
    parseWireDate(`${month}-01`),
  );
}

/**
 * "07-07-2026" — full numeric date in the user's preferred order (issue #13; default `dd-mm-yyyy`).
 * Assembled from the parts rather than from a locale, so the order is the personal choice and
 * never a side effect of the UI language.
 */
export function fmtNumericDate(isoDate: string): string {
  return numericDate(isoDate, getTimeZone(), getDateFormat());
}

/**
 * "7 jul, 14:32" — for timestamps (comments, activity). Full ISO datetime.
 *
 * An instant is rendered in the tenant's zone (CLAUDE.md §8), not the viewer's browser zone, so
 * everyone in the workspace reads the same wall-clock for the same event. The `fmt()` cache keys
 * on the options object, so a different zone gets its own formatter.
 */
export function fmtDateTime(isoDateTime: string): string {
  return fmt({
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    // The clock is the user's personal choice (issue #13), not a side effect of the locale.
    hour12: getClock() === "12h",
    timeZone: getTimeZone(),
  }).format(new Date(isoDateTime));
}

/**
 * A bare wire time ("HH:MM" or "HH:MM:SS") in the user's clock preference (issue #13) —
 * "14:30" for 24h, "2:30 PM" for 12h. Times are wall-clock values, never instants, so no
 * timezone applies. An unreadable value passes through untouched.
 */
export function fmtClockTime(time: string): string {
  const m = /^(\d{1,2}):(\d{2})/.exec(time);
  if (!m) return time;
  const h = Number(m[1]);
  if (h > 23) return time;
  if (getClock() !== "12h") return `${String(h).padStart(2, "0")}:${m[2]}`;
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

/** "€ 1.234" — whole-euro currency in the active locale. */
/**
 * A plain number in the active locale — `12,5` in Dutch, `12.5` in English (CLAUDE.md §8).
 * Trailing zeros are dropped: hours read as `8 u`, not `8,00 u`. Negative values keep their sign,
 * because an over-budget project's remainder is the whole point.
 */
export function fmtNumber(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat(dateLocale(), { maximumFractionDigits }).format(value);
}

/**
 * "€ 1.234" / "€ 87,50" — money in the tenant's currency (#124, per-org like the timezone).
 * Whole amounts drop their fraction (a budget reads "€ 1.234", not "€ 1.234,00"); an amount
 * with cents keeps the currency's own fraction digits, so an € 87,50 hourly rate no longer
 * rounds to € 88 the way the old `maximumFractionDigits: 0` forced it to.
 */
export function fmtMoney(amount: number): string {
  return new Intl.NumberFormat(dateLocale(), {
    style: "currency",
    currency: getCurrency(),
    trailingZeroDisplay: "stripIfInteger",
  }).format(amount);
}

/** Short month labels for chart axes ("jan" … "dec") in the active locale. */
export function monthLabels(): string[] {
  const formatter = fmt({ month: "short", timeZone: "UTC" });
  return Array.from({ length: 12 }, (_, i) => formatter.format(new Date(Date.UTC(2024, i, 1))));
}

/** Full month names ("januari" … "december") — a picker's options, not a chart's axis. */
export function monthNames(): string[] {
  const formatter = fmt({ month: "long", timeZone: "UTC" });
  return Array.from({ length: 12 }, (_, i) => formatter.format(new Date(Date.UTC(2024, i, 1))));
}

/**
 * Weekday names, **Monday first**, indexed the way the API numbers them (`date.weekday()`).
 *
 * From `Intl` rather than seven message keys, exactly like `monthLabels`: a weekday is a
 * calendar fact the platform already knows in every locale, and a hand-translated list is one
 * more place for a newly added language to arrive half-filled. 2024-01-01 is a Monday, which is
 * what anchors index 0 to the API's own numbering.
 */
export function weekdayNames(): string[] {
  const formatter = fmt({ weekday: "long", timeZone: "UTC" });
  return Array.from({ length: 7 }, (_, i) => formatter.format(new Date(Date.UTC(2024, 0, 1 + i))));
}
