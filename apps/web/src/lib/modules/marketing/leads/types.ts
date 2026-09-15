/**
 * The leads dashboard payload (`GET /marketing/companies/{id}/leads`) and the measurement
 * profile it is built from — mirrors `app/modules/marketing/leads/schemas.py` and `profile.py`.
 *
 * A widget is a rendering instruction over rows the API already labelled: `key` is the raw
 * value (what a click posts back as a filter), `label` is what prints. Titles are i18n keys —
 * ours — while a dimension's title and value labels are the tenant's own text.
 */

export type WidgetKind =
  "scorecard" | "bars" | "line" | "donut" | "pivot" | "table" | "funnel" | "combo";
export type Unit = "count" | "money" | "percent" | "ratio" | "text";

export interface LeadColumn {
  key: string;
  title_key?: string | null;
  title?: string | null;
  unit: Unit;
  warn_above?: number | null;
  alarm_above?: number | null;
}

export interface LeadRow {
  key: string;
  label: string;
  values: Record<string, number | null>;
  group?: string | null;
  cells?: Record<string, number> | null;
  texts?: Record<string, string>;
}

export interface LeadSeries {
  dates: string[];
  values: Record<string, number[]>;
  bars: string[];
  units: Record<string, Unit>;
}

export interface LeadWidget {
  key: string;
  part: "leads" | "ads";
  kind: WidgetKind;
  source: "ga4" | "gads";
  title_key: string;
  reports: string[];
  dimension?: string | null;
  dimension_title?: string | null;
  value?: number | null;
  secondary?: number | null;
  secondary_key?: string | null;
  unit: Unit;
  currency?: string | null;
  columns: LeadColumn[];
  rows: LeadRow[];
  row_count?: number | null;
  series?: LeadSeries | null;
  total?: number | null;
  other?: number | null;
  note_key?: string | null;
}

export interface LeadWarning {
  code: string;
  severity: "info" | "warning" | "error";
  details: Record<string, string | number | boolean | null>;
}

export interface LeadCoverage {
  dimension: string;
  title?: string | null;
  total: number;
  not_set: number;
  share: number;
}

export interface LeadBreakpoint {
  date: string;
  text?: string | null;
  severity: "hard" | "soft";
}

export interface LeadFilterOption {
  key: string;
  label: string;
  count: number;
}

export interface LeadFilter {
  dimension: string;
  title?: string | null;
  options: LeadFilterOption[];
  active: string[];
}

export interface LeadUnavailable {
  key: string;
  reason: string;
}

export interface LeadsWindow {
  start: string;
  end: string;
  token: string;
  comparable_from?: string | null;
}

export interface LeadsDashboard {
  company_id: string;
  configured: boolean;
  can_manage: boolean;
  window?: LeadsWindow | null;
  widgets: LeadWidget[];
  unavailable: LeadUnavailable[];
  warnings: LeadWarning[];
  coverage: LeadCoverage[];
  breakpoints: LeadBreakpoint[];
  filters: LeadFilter[];
  disclaimer?: string | null;
  refreshed_at?: string | null;
  ga4_available: boolean;
  ads_available: boolean;
  ga4_deep_link: string;
  ads_deep_link: string;
}

// --- the profile ------------------------------------------------------------------------- //
export type MatchKind = "exact" | "begins_with" | "contains" | "regex";
export const MATCH_KINDS: MatchKind[] = ["exact", "begins_with", "contains", "regex"];

export const ROLES = [
  "request",
  "form_started",
  "form_submitted",
  "form_error",
  "phone_click",
  "email_click",
  "application",
] as const;
export type Role = (typeof ROLES)[number];

export const DIMENSION_KEYS = [
  "service",
  "form_type",
  "form_name",
  "language",
  "page_path",
  "page_title",
  "error_reason",
] as const;
export type DimensionKey = (typeof DIMENSION_KEYS)[number];

export const CHANNEL_GROUPS = ["organic", "ads", "ai", "other"] as const;

/** Every widget the catalog can draw, in display order — mirrors `widgets.WIDGETS`. */
export const WIDGET_KEYS = [
  "contacts",
  "requests",
  "quotes",
  "failures",
  "applications",
  "requests_by_service",
  "requests_by_day",
  "requests_by_channel",
  "requests_by_channel_group",
  "service_by_channel",
  "requests_by_page",
  "requests_by_form_type",
  "requests_by_language",
  "funnel",
  "failures_by_reason",
  "failures_by_page",
  "requests_by_source",
  "ads_cost",
  "ads_conversions",
  "ads_cost_per_conversion",
  "ads_conversion_value",
  "ads_by_day",
  "ads_campaigns",
  "ads_impression_share",
  "ads_conversion_actions",
] as const;

export interface EventMatcher {
  match: MatchKind;
  value: string;
}

export interface DimensionSpec {
  field: string;
  label: Record<string, string>;
  values: Record<string, Record<string, string>>;
  filterable: boolean;
}

export interface Breakpoint {
  date: string;
  description: Record<string, string>;
  severity: "hard" | "soft";
}

export interface LeadProfile {
  ga4_link_id?: string | null;
  gads_link_id?: string | null;
  roles: Partial<Record<Role, EventMatcher[]>>;
  dimensions: Partial<Record<DimensionKey, DimensionSpec>>;
  quote_values: string[];
  ads: { enabled: boolean; action_services: Record<string, string> };
  breakpoints: Breakpoint[];
  channel_groups?: Record<string, string[]> | null;
  hidden_widgets: string[];
  disclaimer: Record<string, string>;
}

export function emptyProfile(): LeadProfile {
  return {
    ga4_link_id: null,
    gads_link_id: null,
    roles: {},
    dimensions: {},
    quote_values: [],
    ads: { enabled: true, action_services: {} },
    breakpoints: [],
    channel_groups: null,
    hidden_widgets: [],
    disclaimer: {},
  };
}

export interface LeadsCatalog {
  ga4_available: boolean;
  ads_available: boolean;
  events: { name: string; count: number }[];
  custom_dimensions: { parameter: string; field: string; display_name: string }[];
  key_events: string[];
  conversion_actions: { name: string; category?: string | null; primary?: boolean | null }[];
  channels: string[];
  unavailable_reason?: string | null;
}

/** The standard GA4 dimensions a profile may name beside the property's own parameters. */
export const STANDARD_DIMENSIONS = ["pagePath", "pageTitle", "landingPage", "language"] as const;
