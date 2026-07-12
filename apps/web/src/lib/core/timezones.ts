/**
 * Timezone options for the branding picker (CLAUDE.md §8).
 *
 * The product has a European bias (`docs/UX.md`), so the common European zones surface first; the
 * full IANA list follows for any cloud tenant elsewhere. The list is derived from the runtime's
 * own tz database via `Intl.supportedValuesOf`, so it never drifts from what the browser (and the
 * API's zoneinfo) can actually resolve. The API validates the chosen value regardless.
 */

// Surfaced at the top of the picker — the zones a Dutch/European agency actually picks.
export const COMMON_EUROPEAN_TIMEZONES = [
  "Europe/Amsterdam",
  "Europe/Brussels",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "Europe/Rome",
  "Europe/London",
  "Europe/Lisbon",
  "Europe/Dublin",
  "Europe/Zurich",
  "Europe/Vienna",
  "Europe/Copenhagen",
  "Europe/Stockholm",
  "Europe/Oslo",
  "Europe/Helsinki",
  "Europe/Warsaw",
  "Europe/Prague",
  "Europe/Athens",
  "Europe/Bucharest",
  "Europe/Istanbul",
  "UTC",
] as const;

/** Every IANA zone this runtime knows, minus the common ones already shown up top. */
export function otherTimeZones(): string[] {
  // `Intl.supportedValuesOf` may be absent from the TS lib target, so reach it defensively.
  const supportedValuesOf = (Intl as { supportedValuesOf?: (key: string) => string[] })
    .supportedValuesOf;
  const supported = typeof supportedValuesOf === "function" ? supportedValuesOf("timeZone") : [];
  const common = new Set<string>(COMMON_EUROPEAN_TIMEZONES);
  return supported.filter((tz) => !common.has(tz));
}
