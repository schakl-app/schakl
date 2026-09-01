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

/**
 * Every source, in display order: Google's three, then the two that are not.
 *
 * The company panel's edit mode already had this list inline; the connect dialog (#338) needs the
 * same one, and two copies of an order is how a sixth source lands in one place and not the other.
 */
export const ALL_SOURCES: MarketingSource[] = ["ga4", "gsc", "gads", "seranking", "rankmath"];
/** The sources a tenant may name for clients (#446) — every one the dashboard can draw. */
export const PORTAL_LABEL_SOURCES: MarketingSource[] = [
  "ga4",
  "gsc",
  "gads",
  "seranking",
  "rankmath",
];

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

/**
 * A client attachment that is **not** a metrics source (#411).
 *
 * The team asked for Tag Manager "in the picker where you have Analytics, Search Console, Ads,
 * SE Ranking and Rank Math", which is right about the *control* and wrong about the
 * *vocabulary*. A container has no marketeer-facing number of its own — no adapter, no daily
 * rows, no KPI row, no drill-down — and the conversions it fires already arrive through GA4. A
 * sixth `MarketingSource` would need a value that `HEADLINE_METRICS`, `ALL_METRICS`,
 * `DRILLDOWNS` and their five API twins each have to be taught to say nothing about, and would
 * still draw a dashboard section with no numbers in it, which is exactly what reads as broken.
 *
 * So the connect control offers **two labelled lists** and this is the second. A connection is
 * attached through its own module's route — Tag Manager's `POST /gtm/containers`, which
 * `gtmActions` already mounts — so there is no marketing link row behind it: the container row
 * *is* the link. That is a stronger form of #338's "the two must not disagree" than mirroring,
 * because two rows cannot disagree when there is only one.
 *
 * The rule for the next one is the question this file has now answered twice: **no daily
 * number means a connection, not a source.**
 */
export type MarketingConnectionKind = "gtm";

/**
 * The declared vocabulary a payload's `kind` may use.
 *
 * Which connect surfaces a *screen* mounts comes from the registry rather than from here
 * (`marketingConnectorsFor`), because that is per-tenant and permission-filtered. This list is
 * the vocabulary itself: it is what keeps the two lists disjoint, which is the property the
 * whole distinction rests on and the one a test can hold.
 */
export const ALL_CONNECTIONS: MarketingConnectionKind[] = ["gtm"];

/** One attached connection as the panel draws it — mirrors the API's `MarketingConnection`. */
export interface MarketingConnectionRow {
  kind: MarketingConnectionKind;
  /** The contributing module's own row id — what its screens address the connection by. */
  id: string;
  /** What anybody quotes: `GTM-XXXXXXX`. */
  external_id: string;
  name: string;
  status: string;
  last_error: string | null;
  /** Staged and never published — the one fact the deleted Tag Manager card carried (#411). */
  pending_changes: number;
  /** How much is live right now, so "12 tags, 3 staged" is one sentence. */
  live_count: number;
  observed_at: string | null;
  /** Into the provider's own console. */
  deep_link: string;
  /** The in-app screen that works on it. */
  href: string;
}

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
  /** The vendor console. Empty for a portal login (#447) — never drawn without one. */
  deep_link: string;
  /** What *this reader* calls the source (#446): set for a portal login (the tenant's own
   *  client-facing name, else a vendor-free default), `null` for staff, who read the product
   *  name. Consumers print `label ?? sourceLabel(source)`. */
  label?: string | null;
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
  /** Attachments that carry no metrics (#411). Only the company panel asks for them. */
  connections?: MarketingConnectionRow[];
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
  /**
   * For a **site-key** source (Rank Math) only: which of the four prerequisites is the first
   * unmet one, `"ready"` when none is (#435). `null` for every other source, which has no
   * per-website setup to be partway through.
   *
   * It is what `configured` structurally could not be. One boolean answered "there is no
   * credential" and "the credential was refused" identically, and an empty `accounts` answered
   * "Rank Math is not installed" and "this client has no brand yet" identically — four
   * different jobs, in two products, for three different people.
   */
  setup_stage?: string | null;
  /** WordPress's own words about the refusal above. A quote: rendered, never translated. */
  setup_detail?: string | null;
  /** Deep links into the client's own wp-admin: `app_passwords`, `plugins`, `ai_visibility`. */
  setup_links?: Record<string, string>;
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

/**
 * One source a client has linked, as the picker's chips render it.
 *
 * `state` is three-way, not the panel's four: "disconnected" is read from the absence of a
 * *Google* connection, which two of the five sources never have — see the API schema.
 */
export interface MarketingClientSource {
  source: MarketingSource;
  /** Links of this source (two properties for two websites is ordinary). */
  links: number;
  state: "ok" | "pending" | "error";
}

/** A client with at least one linked source — one tile on the Marketing picker. */
export interface MarketingClientRow {
  company_id: string;
  company_name: string;
  sources: MarketingClientSource[];
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
  // `avg_sentiment` is the one of the five that stays out of the panel: it is a 0-100 quality
  // signal, not a size, and next to a mentions count and a score out of 100 a fourth bare
  // number is one number too many to tell apart. It keeps its place in the tab's full list
  // below, where it prints as "46%" and carries the sentence that says what 46 would be.
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
