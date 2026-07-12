/**
 * Locale-aware date/number formatting (CLAUDE.md §8) with a European preference:
 * the UI locale maps to a European Intl locale (en → en-GB), so English users still get
 * day-month ordering and 24-hour clocks. Date-only ISO strings are wall-clock values and
 * are formatted in UTC so they never shift a day.
 */
import { getClock, getDateFormat } from "$lib/core/dateformat";
import { getTimeZone } from "$lib/core/timezone";
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

function dateOnly(iso: string): Date {
  return new Date(iso + "T00:00:00Z");
}

/** "7 jul" — for due dates, list rows, chips. Takes a date-only ISO string. */
export function fmtDayMonth(isoDate: string): string {
  return fmt({ day: "numeric", month: "short", timeZone: "UTC" }).format(dateOnly(isoDate));
}

/** "ma 7" — weekday + day, for grid column headers. Date-only ISO string. */
export function fmtWeekdayDay(isoDate: string): string {
  return fmt({ weekday: "short", day: "numeric", timeZone: "UTC" }).format(dateOnly(isoDate));
}

/** "ma" — weekday abbreviation. Date-only ISO string. */
export function fmtWeekdayShort(isoDate: string): string {
  return fmt({ weekday: "short", timeZone: "UTC" }).format(dateOnly(isoDate));
}

/** "maandag 7 juli" — the day heading. Date-only ISO string. */
export function fmtLongDay(isoDate: string): string {
  return fmt({ weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }).format(
    dateOnly(isoDate),
  );
}

/** "juli 2026" — calendar popover heading. Takes a "yyyy-mm" month. */
export function fmtMonthYear(month: string): string {
  return fmt({ month: "long", year: "numeric", timeZone: "UTC" }).format(dateOnly(`${month}-01`));
}

/**
 * "07-07-2026" — full numeric date in the user's preferred order (issue #13; default `dd-mm-yyyy`).
 * Date-only ISO string. Assembled from the parts rather than from a locale, so the order is the
 * personal choice and never a side effect of the UI language.
 */
export function fmtNumericDate(isoDate: string): string {
  const d = dateOnly(isoDate);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const yyyy = String(d.getUTCFullYear()).padStart(4, "0");
  switch (getDateFormat()) {
    case "yyyy-mm-dd":
      return `${yyyy}-${mm}-${dd}`;
    case "mm-dd-yyyy":
      return `${mm}-${dd}-${yyyy}`;
    default:
      return `${dd}-${mm}-${yyyy}`;
  }
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

export function fmtMoney(amount: number): string {
  return new Intl.NumberFormat(dateLocale(), {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(amount);
}

/** Short month labels for chart axes ("jan" … "dec") in the active locale. */
export function monthLabels(): string[] {
  const formatter = fmt({ month: "short", timeZone: "UTC" });
  return Array.from({ length: 12 }, (_, i) => formatter.format(new Date(Date.UTC(2024, i, 1))));
}
