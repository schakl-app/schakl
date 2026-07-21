/**
 * Phone-number helpers (issue #256), shared by `PhoneInput` and every read view.
 *
 * Built on `libphonenumber-js/min` — the compact metadata build (the full one is several times
 * larger and only adds geocoding-grade precision this UI never shows). The country list comes
 * from that same metadata rather than a hand-built ISO-3166 table, and display names come from
 * `Intl.DisplayNames`, so ~240 country names never enter the message catalogs.
 *
 * Client-side parsing here is feedback only; the API's `phonenumbers` check is the gate.
 */
import {
  type CountryCode,
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
} from "libphonenumber-js/min";

import { getLocale } from "$lib/paraglide/runtime";

export interface PhoneCountry {
  code: CountryCode;
  name: string;
  dial: string;
}

/**
 * The default picker country, resolved like the locale chain in §8 — browser first (the one
 * new-to-this-app source, per the issue), then the active UI locale's implied region, then the
 * instance fallback. `maximize()` fills the likely region for a bare language tag (nl → NL).
 */
export function defaultPhoneCountry(): CountryCode {
  const candidates: (string | undefined)[] = [];
  if (typeof navigator !== "undefined") {
    candidates.push(navigator.language, Intl.DateTimeFormat().resolvedOptions().locale);
  }
  candidates.push(getLocale());
  const known = new Set<string>(getCountries());
  for (const tag of candidates) {
    if (!tag) continue;
    try {
      const region = new Intl.Locale(tag).maximize().region;
      if (region && known.has(region)) return region as CountryCode;
    } catch {
      // An unparseable tag just falls through to the next candidate.
    }
  }
  return "NL";
}

/** Every country the metadata knows, named in the active UI locale, sorted for the picker. */
export function phoneCountries(): PhoneCountry[] {
  const locale = getLocale();
  const names = new Intl.DisplayNames([locale], { type: "region" });
  return getCountries()
    .map((code) => ({
      code,
      name: names.of(code) ?? code,
      dial: getCountryCallingCode(code),
    }))
    .sort((a, b) => a.name.localeCompare(b.name, locale));
}

/**
 * How a stored phone reads: E.164 renders international ("+31 6 12345678"); a legacy freeform
 * value (pre-#256 contacts) is shown exactly as stored — reinterpreting it would guess its
 * country, which is the one thing the retrofit promised not to do.
 */
export function formatPhone(value: string | null | undefined): string {
  if (!value) return "";
  if (!value.startsWith("+")) return value;
  return parsePhoneNumberFromString(value)?.formatInternational() ?? value;
}
