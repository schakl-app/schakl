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
