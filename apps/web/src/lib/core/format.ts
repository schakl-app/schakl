/**
 * Locale-aware date/number formatting (CLAUDE.md §8) with a European preference:
 * the UI locale maps to a European Intl locale (en → en-GB), so English users still get
 * day-month ordering and 24-hour clocks. Date-only ISO strings are wall-clock values and
 * are formatted in UTC so they never shift a day.
 */
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

/** "07-07-2026" — full numeric date, European order. Date-only ISO string. */
export function fmtNumericDate(isoDate: string): string {
  return fmt({ day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }).format(
    dateOnly(isoDate),
  );
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
    timeZone: getTimeZone(),
  }).format(new Date(isoDateTime));
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
