/**
 * The shapes the Google Ads read surface returns.
 *
 * Two of them are worth restating here, because they are where a UI most easily lies about the
 * data underneath it:
 *
 * - **`ctr` and `conversion_rate` are fractions.** `0.0453` is 4,53 %. Multiplying happens in
 *   `format.ts` and nowhere else.
 * - **`null` is not `0`.** A null ratio means "not computable" — no impressions, no conversions
 *   — and rendering it as `0,00` states a measurement that was never made.
 */

export interface GoogleAdsMetrics {
  impressions: number;
  clicks: number;
  cost: number;
  conversions: number;
  conversions_value: number;
  all_conversions: number;
  ctr: number | null;
  average_cpc: number | null;
  conversion_rate: number | null;
  cost_per_conversion: number | null;
  value_per_conversion: number | null;
}

export interface GoogleAdsPeriod {
  date_from: string;
  date_to: string;
  days: number;
  token: string | null;
}

export interface GoogleAdsAccountBrief {
  id: string;
  customer_id: string;
  customer_id_formatted: string;
  descriptive_name: string;
  company_id: string | null;
}

export interface GoogleAdsReport {
  account: GoogleAdsAccountBrief;
  period: GoogleAdsPeriod | null;
  currency: string | null;
  account_timezone: string | null;
  fetched_at: string;
  row_count: number;
  /** i18n keys. Truncation, a shortened window, a geo fallback — reported here and nowhere else. */
  warnings: string[];
  totals: GoogleAdsMetrics | null;
  rows: Record<string, unknown>[];
  extra: Record<string, unknown>;
}

export interface GoogleAdsChangeAmount {
  from?: number | null;
  to?: number | null;
  absolute?: number | null;
  /** `null` when the baseline was zero: a percentage against nothing is undefined. */
  relative?: number | null;
}

/**
 * The trend read's own shape. A separate type rather than optional fields on
 * `GoogleAdsReport`, because it is a genuinely different answer: it comes from schakl's stored
 * rows rather than from Google, it always has a period, and it carries the compared window's
 * dates — which is what makes any percentage on the screen checkable (#312).
 */
export interface GoogleAdsTrendReport {
  account: GoogleAdsAccountBrief;
  period: GoogleAdsPeriod;
  compared_with: GoogleAdsPeriod;
  compare_mode: string;
  currency: string | null;
  totals: GoogleAdsMetrics;
  previous_totals: GoogleAdsMetrics;
  change: Record<string, GoogleAdsChangeAmount | null>;
  series: { date: string; metrics: GoogleAdsMetrics }[];
  breakdown: Record<string, unknown>[];
  /** Days with no stored row — "not synced yet", never "no spend". */
  missing_days: number;
  warnings: string[];
}
