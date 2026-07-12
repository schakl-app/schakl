/**
 * Currency options for the branding picker (issue #124, CLAUDE.md §8).
 *
 * The product has a European bias (`docs/UX.md`), so the common European codes surface first; the
 * full ISO 4217 list follows for any agency elsewhere. The list mirrors the API's
 * `app.core.currency.ISO_4217` (active codes, no funds/metal codes) rather than
 * `Intl.supportedValuesOf("currency")`, which also returns historical and non-currency codes the
 * API would reject. The API validates the chosen value regardless.
 */

// Surfaced at the top of the picker — what a European agency actually picks.
export const COMMON_CURRENCIES = ["EUR", "USD", "GBP", "CHF", "DKK", "SEK", "NOK", "PLN", "CZK"] as const;

//: Active ISO 4217 alphabetic codes — keep in sync with the API's `app/core/currency.py`.
const ISO_4217 = [
  "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
  "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
  "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
  "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
  "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
  "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
  "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
  "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL",
  "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR",
  "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
  "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR",
  "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
  "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL", "THB",
  "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
  "USD", "UYU", "UZS", "VED", "VES", "VND", "VUV", "WST", "XAF", "XCD",
  "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWG",
];

/** Every ISO 4217 code, minus the common ones already shown up top. */
export function otherCurrencies(): string[] {
  const common = new Set<string>(COMMON_CURRENCIES);
  return ISO_4217.filter((code) => !common.has(code));
}

/** "EUR — euro" — the code stays primary (it is what gets stored); the name disambiguates. */
export function currencyLabel(code: string, locale: string): string {
  try {
    const name = new Intl.DisplayNames(locale, { type: "currency" }).of(code);
    return name && name !== code ? `${code} — ${name}` : code;
  } catch {
    return code;
  }
}
