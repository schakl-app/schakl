/** Web-side shapes of the marketing API payloads (epic #134), mirroring the Pydantic schemas. */

export type MarketingSource = "ga4" | "gsc" | "gads";

export interface KpiValue {
  current: number;
  previous: number;
  delta_pct: number | null;
  lower_is_better: boolean;
}

export interface SeriesData {
  dates: string[];
  metrics: Record<string, number[]>;
}

export interface SourceMetrics {
  link_id: string;
  source: MarketingSource;
  display_name: string;
  external_id: string;
  /** The client website this link measures (`null` = client-level) — the tab groups on it. */
  website_id: string | null;
  website_name: string | null;
  health: "ok" | "pending" | "error" | "disconnected";
  last_error: string | null;
  last_synced_at: string | null;
  currency: string | null;
  deep_link: string;
  primary_metric: string;
  kpis: Record<string, KpiValue>;
  series: SeriesData;
  channels: Record<string, number> | null;
  /** Ordered, visible tile keys after the client's layout applied (#192). */
  tiles: string[];
  /** Per-tile label overrides, `{metric: {locale: label}}` — the tenant's naming (#192). */
  tile_labels: Record<string, Record<string, string>>;
  /** Enabled drill-down kinds after the layout applied (#192). */
  drilldowns: string[];
  /** This source is hidden from the client's dashboard (#192) — only ever set for a manager,
   *  so edit mode can list it and offer to re-enable it. The portal never receives it. */
  hidden?: boolean;
}

/** One source's stored layout (#192); `null`/absent fields mean "not curated". */
export interface SourceLayout {
  tiles?: string[] | null;
  labels?: Record<string, Record<string, string>>;
  drilldowns?: string[] | null;
  chart_metric?: string | null;
  /** GA4 only: per key-event display labels keyed by the GA4 `eventName`, `{eventName: {locale: label}}`. */
  event_labels?: Record<string, Record<string, string>>;
  /** Drop this whole source section from the client's dashboard (not just its tiles). */
  hidden?: boolean;
}

export interface CompanyLayout {
  sources: Record<string, SourceLayout>;
}

/** One source's in-progress edit state while the dashboard's edit mode is on. Owned by
 *  `MarketingDashboard`, mutated in place by the section it hands it to; every mutation
 *  calls back so the whole layout persists at once (like the My Day board). */
export interface SourceEditState {
  /** Visible tiles in display order — the dnd items (`svelte-dnd-action` needs `{id}`). */
  tiles: { id: string }[];
  /** Per-tile label overrides for every metric key (empty string = no override). */
  labels: Record<string, { nl: string; en: string }>;
  /** Enabled drill-down kinds. */
  drilldowns: string[];
  /** Default charted metric ("" = automatic). */
  chart_metric: string;
  /** GA4 only: per key-event labels keyed by the raw `eventName`. */
  event_labels: Record<string, { nl: string; en: string }>;
  /** Hide this whole source from the client's dashboard. */
  hidden: boolean;
}

export interface CompanyMarketing {
  company_id: string;
  range_days: number;
  sources: SourceMetrics[];
  needs_connection: boolean;
  can_manage: boolean;
  /** Whether GA4 key events / conversions are shown for this client (#134). */
  show_key_events: boolean;
  /** The stored layout (#192), present for a caller who may manage it (`can_manage`). */
  layout?: CompanyLayout | null;
  /** The client's websites — picker options for new links and the tab's group labels. */
  websites: { id: string; name: string }[];
  forbidden?: boolean;
}

export interface AvailableAccount {
  external_id: string;
  display_name: string;
  account_hint: string | null;
  config: Record<string, unknown>;
  already_linked: boolean;
}

export interface AccountsResponse {
  source: MarketingSource;
  connected: boolean;
  has_scope: boolean;
  configured: boolean;
  accounts: AvailableAccount[];
  error: string | null;
  connect_flag: string;
}

export interface DrilldownRow {
  label: string;
  /** The row's stable id — for GA4 key events the raw `eventName` (#192); `null` otherwise. */
  key?: string | null;
  href: string | null;
  metrics: Record<string, number>;
}

export interface DrilldownResponse {
  source: MarketingSource;
  kind: string;
  columns: string[];
  rows: DrilldownRow[];
  available: boolean;
  unavailable_reason: string | null;
  deep_link: string;
}

export interface OverviewRow {
  company_id: string;
  company_name: string;
  sources_present: MarketingSource[];
  metrics: Record<string, KpiValue>;
  /** Whether GA4 key events / conversions are shown for this client — drives the grid toggle. */
  show_key_events: boolean;
}

/** The headline metrics each source leads with (panel KPI row + sparkline order). */
export const HEADLINE_METRICS: Record<MarketingSource, string[]> = {
  ga4: ["sessions", "totalUsers", "conversions", "engagementRate"],
  gsc: ["clicks", "impressions", "position", "ctr"],
  gads: ["cost", "clicks", "conversions", "conversionsValue"],
};

/** Every metric a source carries, in display order (mirrors the API's METRICS_BY_SOURCE). */
export const ALL_METRICS: Record<MarketingSource, string[]> = {
  ga4: ["sessions", "totalUsers", "newUsers", "keyEvents", "conversions", "engagementRate", "totalRevenue"],
  gsc: ["clicks", "impressions", "ctr", "position"],
  gads: ["cost", "clicks", "impressions", "conversions", "conversionsValue"],
};

/** The tier-2 drill-downs each source offers (mirrors the adapter's `drilldowns`). */
export const DRILLDOWNS: Record<MarketingSource, string[]> = {
  ga4: ["top_pages", "channels", "devices", "key_events"],
  gsc: ["top_queries", "top_pages", "movers"],
  gads: ["campaigns"],
};

/** The connect-flow query flag that adds each source's scope (for the connect deep-link). */
export const CONNECT_FLAG: Record<MarketingSource, string> = {
  ga4: "include_analytics",
  gsc: "include_search_console",
  gads: "include_ads",
};

/** The deep-link that walks a connection up to the given sources' scopes (no second OAuth). */
export function connectHref(sources: MarketingSource[]): string {
  const flags = sources.map((s) => `${CONNECT_FLAG[s]}=1`).join("&");
  return `/api/v1/google/oauth/connect?${flags}`;
}
