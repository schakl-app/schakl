/** Web-side shapes of the marketing API payloads (epic #134), mirroring the Pydantic schemas. */

/**
 * A linkable data source. `seranking` is the one that is not Google (#300) — it rides one
 * agency API key rather than a per-user OAuth grant, which is why the picker below teaches
 * "not configured" for it instead of offering a Connect link that would lead nowhere.
 *
 * `rankmath` is the third credential kind again (docs/WORDPRESS.md): one WordPress application
 * password per **website**, so its picker cannot be answered at all until a site is named. The
 * API mirrors these three in `sources/base.py`'s `AUTH_*`; this file is the only place the web
 * knows them, so a fourth kind belongs here and in the two constants below, nowhere else.
 */
export type MarketingSource = "ga4" | "gsc" | "gads" | "seranking" | "rankmath";

/** Sources whose credential is an org-level API key, not the shared Google consent. */
export const ORG_KEY_SOURCES: readonly MarketingSource[] = ["seranking"];

/**
 * Sources whose credential belongs to one client **website**, not to the agency.
 *
 * The picker consults this before it fetches: a brand list is a question about one site, so
 * asking it with no site named is not an empty result, it is a question nobody asked. Linking
 * without one is refused by the API too (`errors.marketing_rankmath_website_required`) — this
 * is the half that stops the user reaching that refusal.
 */
export const SITE_KEY_SOURCES: readonly MarketingSource[] = ["rankmath"];

export interface KpiValue {
  current: number;
  previous: number;
  delta_pct: number | null;
  lower_is_better: boolean;
}

/** Which comparison a dashboard measures against (#312) — mirrors `app.core.periods`. */
export type ComparePeriod = "year" | "previous";

export const COMPARE_PERIODS: readonly ComparePeriod[] = ["year", "previous"];

/**
 * The two spans behind every `delta_pct` in a payload (#312).
 *
 * Carried rather than re-derived in the browser: the API resolved the client's setting, the
 * org's default and the org's timezone to pick these dates, and a second computation here would
 * be a second opinion — which is the bug the issue is about, one screen labelling its delta
 * "vorige periode" while the document built from the same numbers said "vorig jaar".
 */
export interface CompareWindow {
  mode: ComparePeriod;
  /** The period the numbers cover. */
  current_start: string;
  current_end: string;
  /** The span they were measured against. */
  start: string;
  end: string;
}

export interface SeriesData {
  dates: string[];
  metrics: Record<string, number[]>;
}

/** Whose Google grant a link syncs through — shown so a colleague sees "via Stan", not silence. */
export interface ConnectionOwner {
  user_id: string;
  /** The colleague's own name (their login e-mail when they have set no name). */
  name: string;
  /** The connected Google account — routinely a different address from the login. */
  email: string;
  /** The viewer's own connection. */
  is_me: boolean;
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
  /** Whose Google connection syncs this source (`null` once that connection is gone). */
  connection_owner: ConnectionOwner | null;
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
  /** Per-tile label overrides for every metric key, `{metric: {locale: label}}` (empty string =
   *  no override). Keyed by locale rather than by `{nl, en}` fields: the editor writes whichever
   *  language the surface's `I18nLocaleSwitcher` is on, and a new locale is a JSON file, not a
   *  type change (CLAUDE.md §8). */
  labels: Record<string, Record<string, string>>;
  /** Enabled drill-down kinds. */
  drilldowns: string[];
  /** Default charted metric ("" = automatic). */
  chart_metric: string;
  /** GA4 only: per key-event labels keyed by the raw `eventName`, `{event: {locale: label}}`. */
  event_labels: Record<string, Record<string, string>>;
  /** Hide this whole source from the client's dashboard. */
  hidden: boolean;
}

export interface CompanyMarketing {
  company_id: string;
  range_days: number;
  /** The spans every delta below was computed from (#312) — what the screen names. */
  compare: CompareWindow;
  /** The *stored* per-client override; `null` = follows the org default. Manager-only. */
  compare_setting?: ComparePeriod | null;
  /** The org default, so the editor's inherit option can say what it inherits. */
  compare_default: ComparePeriod;
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
  /** Colleagues whose connection already reaches this source — so the empty state can say
   *  "already connected via X" instead of a bare "not connected". */
  connected_via: ConnectionOwner[];
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
  seranking: ["avg_position", "top10", "top3", "keywords_ranking"],
  // `avg_sentiment` is the one of the five that stays out of the panel: it is a −1…1 quality
  // signal, not a size, and a client glancing at four tiles reads "0,46" as a bad score rather
  // than as a mildly positive tone. It keeps its place in the tab's full list below.
  rankmath: ["ai_visibility_score", "mentions", "citations", "brand_rank"],
};

/** Every metric a source carries, in display order (mirrors the API's METRICS_BY_SOURCE). */
export const ALL_METRICS: Record<MarketingSource, string[]> = {
  ga4: [
    "sessions",
    "totalUsers",
    "newUsers",
    "keyEvents",
    "conversions",
    "engagementRate",
    "totalRevenue",
  ],
  gsc: ["clicks", "impressions", "ctr", "position"],
  gads: ["cost", "clicks", "impressions", "conversions", "conversionsValue"],
  seranking: ["avg_position", "top3", "top10", "top30", "keywords_ranking", "keywords_tracked"],
  rankmath: ["ai_visibility_score", "mentions", "citations", "avg_sentiment", "brand_rank"],
};

/** The tier-2 drill-downs each source offers (mirrors the adapter's `drilldowns`). */
export const DRILLDOWNS: Record<MarketingSource, string[]> = {
  ga4: [
    "top_pages",
    "channels",
    "devices",
    "key_events",
    "organic_sources",
    "social_sources",
    "referral_sources",
  ],
  gsc: ["top_queries", "top_pages", "movers"],
  gads: ["campaigns"],
  seranking: ["keywords", "keyword_groups", "audit", "ai_search"],
  rankmath: ["competitors", "queries"],
};

/**
 * The deep-link that grants this module its Google access — **all three sources at once**.
 *
 * Asking per source is what made connecting Google a three-times-in-a-row chore: each picker
 * linked its own scope, so Analytics, Search Console and Ads each cost a full trip through
 * Google's consent screen for one module the agency enabled once. `include_marketing` unions
 * them into a single consent, and `include_granted_scopes` still means a connection that
 * already holds Calendar or Gmail keeps it.
 *
 * `next` is where to land afterwards — the page the user was actually on. The API only honours
 * a site-relative path, so pass `page.url.pathname + page.url.search`, never an absolute URL.
 */
export function connectHref(next?: string): string {
  const params = new URLSearchParams({ include_marketing: "1" });
  if (next) params.set("next", next);
  return `/api/v1/google/oauth/connect?${params.toString()}`;
}
