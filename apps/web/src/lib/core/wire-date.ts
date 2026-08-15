/**
 * Which zone a wire value's calendar date is read in — the one distinction every date formatter
 * in `format.ts` has to make, kept here so it can be tested without the app's runtime seams.
 *
 * The API sends two shapes and they are not interchangeable. A **date** (`2026-07-07`) is a
 * wall-clock value — a due date, a contract start, an expiry — with no zone of its own; it is
 * read in UTC so it never slips a day. An **instant** (`2026-07-07T09:12:33Z`) is a moment, and
 * CLAUDE.md §8 says it renders in the *tenant's* zone, so everyone in the workspace reads the
 * same wall clock for the same event.
 *
 * Every formatter in `format.ts` used to assume the first shape: it pinned `timeZone: "UTC"` and
 * parsed by concatenating a midnight onto the string. Handed an instant, it built
 * `2026-07-07T09:12:33ZT00:00:00Z`, which is an Invalid Date, which `fmtNumericDate` printed as
 * **`NaN-NaN-0NaN`** — day and month are `NaN`, and the year is `String(NaN).padStart(4, "0")`.
 * That shipped on five screens at once (the three Google Ads panels' "gecontroleerd", the cloud
 * console's lifecycle dates, the domain health card, the HR document list), because nothing in
 * the build could see it: both shapes are `string`, so the types agree, `svelte-check` is happy,
 * and the garbage only appears on a row that actually carries a timestamp.
 *
 * So the value decides, and no caller has to say which it passed: an instant carries a `T`.
 * A caller that wants the *time* on screen still asks for it (`fmtDateTime`); this is only about
 * not printing rubbish when a date formatter is handed a moment.
 */
import type { DateFormat } from "$lib/core/dateformat";

/** True when the wire value carries a time — i.e. it is an instant, not a wall-clock date. */
export function isInstant(value: string): boolean {
  return value.includes("T");
}

/** Either shape as a `Date`. A date-only value becomes midnight UTC, as it always did. */
export function parseWireDate(value: string): Date {
  return new Date(isInstant(value) ? value : `${value}T00:00:00Z`);
}

/**
 * The zone the value's calendar date must be read in: the tenant's for an instant, UTC for a
 * wall-clock date. Pass it straight to `Intl.DateTimeFormat`'s `timeZone`.
 */
export function wireZone(value: string, tenantZone: string): string {
  return isInstant(value) ? tenantZone : "UTC";
}

// Locale-independent on purpose: the *parts* are digits, and only the order below is a choice.
// `en-US` with 2-digit month/day is just a padded-latin-digits formatter here; the user's locale
// must not decide whether a numeric date reads `07` or `٠٧`.
const _parts = new Map<string, Intl.DateTimeFormat>();

function partsFor(zone: string): Intl.DateTimeFormat {
  let formatter = _parts.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: zone,
    });
    _parts.set(zone, formatter);
  }
  return formatter;
}

/** The zero-padded `{day, month, year}` of a wire value, resolved in the zone that value implies. */
export function wireDateParts(
  value: string,
  tenantZone: string,
): { day: string; month: string; year: string } {
  const parts = partsFor(wireZone(value, tenantZone)).formatToParts(parseWireDate(value));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  return {
    day: part("day").padStart(2, "0"),
    month: part("month").padStart(2, "0"),
    year: part("year").padStart(4, "0"),
  };
}

/**
 * `07-07-2026` — a wire value in the user's preferred order (issue #13; default `dd-mm-yyyy`).
 * Assembled from the parts rather than from a locale, so the order is the personal choice and
 * never a side effect of the UI language.
 */
export function numericDate(value: string, tenantZone: string, order: DateFormat): string {
  const { day, month, year } = wireDateParts(value, tenantZone);
  switch (order) {
    case "yyyy-mm-dd":
      return `${year}-${month}-${day}`;
    case "mm-dd-yyyy":
      return `${month}-${day}-${year}`;
    default:
      return `${day}-${month}-${year}`;
  }
}
