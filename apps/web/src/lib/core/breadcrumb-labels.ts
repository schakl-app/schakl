/**
 * What a breadcrumb segment is *called*, and what a record is — the half of the crumb row that
 * needs no translator and no browser.
 *
 * It is a file of its own so `tests/unit/breadcrumbs.test.ts` can sweep the real route tree and
 * fail on a segment nothing names. `breadcrumbs.ts` imports `$lib/paraglide/messages` through
 * `t()`, which node's bare test runner cannot resolve; keeping the maps here is what makes the
 * rule enforceable instead of audited once and drifted from afterwards — the same reason
 * `settings-nav.ts` holds the Instellingen registry rather than each screen.
 */
import { settingsTitleKeys } from "./settings-nav.ts";

/**
 * First path segment → label key, for the sections that contribute no nav item. A section that
 * *does* have one is labelled from the nav registry instead, so a tenant's rename (#169) reaches
 * the crumb rather than leaving it contradicting the menu directly above it.
 */
export const ROOTS: Record<string, string> = {
  calendar: "nav.calendar",
  companies: "nav.companies",
  contacts: "nav.contacts",
  tasks: "nav.tasks",
  time: "nav.time",
  projects: "nav.projects",
  domains: "nav.domains",
  websites: "nav.websites",
  subscriptions: "nav.subscriptions",
  invoices: "invoicing.invoices",
  quotes: "invoicing.quotes",
  marketing: "nav.marketing",
  interactions: "nav.interactions",
  leave: "nav.leave",
  reports: "nav.reports",
  overview: "nav.overview",
  notifications: "notifications.title",
  me: "hr.me.title",
  settings: "settings.title",
  instance: "nav.instance",
  ai: "ai.assistant.title",
};

/**
 * Settings slug → its screen title key. Taken from the screen registry rather than re-typed, so a
 * renamed card renames its crumb too — this map had already drifted into a third copy of the
 * Instellingen surface. `subscriptions` is the one extra: it is a 301 to `/subscriptions/templates`
 * (#229), so the registry points outside `/settings` and the slug still needs a label.
 */
export const SETTINGS: Record<string, string> = {
  ...settingsTitleKeys(),
  subscriptions: "settings.subscriptions.title",
};

/** Known non-id tail segments. */
export const TAILS: Record<string, string> = {
  new: "common.new",
  print: "common.print",
  templates: "tasks.nav.templates",
  team: "leave.team.title",
  runs: "automation.runs",
  marketing: "marketing.tab.title",
  revenue: "overview.tab.revenue",
  productivity: "overview.tab.productivity",
};

/** Root-specific tail labels — the same segment reads differently per section (#229). */
export const TAILS_BY_ROOT: Record<string, Record<string, string>> = {
  invoices: {
    uninvoiced: "invoicing.uninvoiced.title",
  },
  subscriptions: {
    templates: "settings.subscriptions.templates_heading",
    types: "settings.subscriptions.types_heading",
  },
  domains: {
    "tld-prices": "domains.tld_prices.title",
  },
  companies: {
    // This client's own reporting profile, not the org-wide Instellingen screen of the same name.
    reporting: "reporting.profile.title",
  },
  marketing: {
    "google-ads": "nav.google_ads",
    decisions: "google_ads.view.decisions",
    policy: "settings.google_ads.policy.title",
  },
};

/** How many ancestors a contextual trail may carry before the row stops being readable. */
export const MAX_ANCESTORS = 3;

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The message key for one literal segment, or `null` if nothing names it. */
export function literalLabelKey(segments: string[], index: number): string | null {
  const segment = segments[index];
  if (index === 0) return ROOTS[segment] ?? null;
  if (segments[0] === "settings" && index === 1) return SETTINGS[segment] ?? null;
  return TAILS_BY_ROOT[segments[0]]?.[segment] ?? TAILS[segment] ?? null;
}

/**
 * The route's parameter name per path segment: `null` for a literal, the parameter's name for a
 * dynamic one, `undefined` where there is no route id to align against. Group segments (`(app)`)
 * exist in the route id and never in the URL, so they are dropped before aligning.
 *
 * This replaced an "does it look like a UUID?" test, which was a guess that was already wrong: a
 * Google Ads account id is a number, so `/marketing/google-ads/4155551234` printed the raw
 * customer id as its own crumb. The id-shaped test survives in `breadcrumbs.ts` only as the
 * fallback for a page with no route id at all.
 */
export function routeParamNames(routeId: string | null | undefined): (string | null)[] {
  return (routeId ?? "")
    .split("/")
    .filter((segment) => segment && !(segment.startsWith("(") && segment.endsWith(")")))
    .map((segment) =>
      segment.startsWith("[") ? segment.replace(/^\[+\.*/, "").replace(/[\].]+$/g, "") : null,
    );
}

type Fields = Record<string, unknown>;

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/**
 * The detail-page data keys that hold a record, with the name each one goes by.
 *
 * Order is priority, and every candidate must carry a string `id` — without that guard the Google
 * Ads report page, whose load streams a promise under the key `report`, would be read as a
 * reporting document and label its own crumb "…".
 */
const RECORDS: {
  key: string;
  type: string;
  label: (r: Fields) => string | null;
  /** Used when the record exists but has no name yet — an unissued invoice has no number. */
  fallbackKey?: string;
}[] = [
  { key: "company", type: "company", label: (r) => str(r.name) },
  {
    key: "contact",
    type: "contact",
    label: (r) => str([r.first_name, r.last_name].filter(Boolean).join(" ")),
  },
  { key: "project", type: "project", label: (r) => str(r.name) },
  { key: "task", type: "task", label: (r) => str(r.title) },
  { key: "domain", type: "domain", label: (r) => str(r.name) },
  {
    key: "website",
    type: "website",
    // A website has no name of its own — the host it answers on is the name, resolved exactly as
    // its page title and its list row do (`root` decides whether the `www.` prefix is part of it).
    label: (r) => {
      const host = str(r.domain_name);
      return host && (r.root ? host : `www.${host}`);
    },
  },
  {
    key: "invoice",
    type: "invoice",
    label: (r) => str(r.number),
    fallbackKey: "invoicing.status.draft",
  },
  {
    key: "quote",
    type: "quote",
    label: (r) => str(r.number),
    fallbackKey: "invoicing.status.draft",
  },
  { key: "report", type: "report", label: (r) => str(r.title) ?? str(r.company_name) },
  { key: "account", type: "ads_account", label: (r) => str(r.descriptive_name) },
  { key: "org", type: "org", label: (r) => str(r.name) },
  { key: "rule", type: "automation_rule", label: (r) => str(r.name) },
  {
    key: "role",
    type: "role",
    label: (r) => {
      const names = (r.name_i18n ?? {}) as Record<string, string>;
      return str(names.nl) ?? str(names.en) ?? str(r.key);
    },
  },
];

/** What the page in front of the visitor is about, in the shape its own load left it. */
export interface PageRecord {
  type: string;
  label: string;
  record: Fields;
}

/**
 * The one record this page is about, or `null` on a list, a form or a settings screen.
 *
 * `translate` is passed in rather than imported so this file stays free of the Paraglide runtime,
 * which is what lets the route sweep run it.
 */
export function pageRecord(data: Fields, translate: (key: string) => string): PageRecord | null {
  for (const spec of RECORDS) {
    const value = data[spec.key];
    if (!value || typeof value !== "object") continue;
    const record = value as Fields;
    if (typeof record.id !== "string") continue;
    const label = spec.label(record) ?? (spec.fallbackKey ? translate(spec.fallbackKey) : null);
    if (label) return { type: spec.type, label, record };
  }
  return null;
}

/**
 * One record on the way to the current page. `type` + `id` are what the next page checks itself
 * against; `label` and `href` are what gets drawn once it has.
 */
export interface CrumbLink {
  type: string;
  id: string;
  label: string;
  href: string;
}

/**
 * Which column on a record points back at an ancestor of that type. This is the whole of the
 * "is the way in true?" check — an ancestor the record does not name is never drawn.
 */
const PARENT_FK: Record<string, string> = {
  company: "company_id",
  project: "project_id",
  domain: "domain_id",
  contact: "contact_id",
  task: "task_id",
};

export function isParentOf(link: CrumbLink, record: PageRecord): boolean {
  // A tab of a record is not a child of it: `/companies/<id>/reporting` is still about that
  // company, and without this the row would name it twice in a row.
  if (link.type === record.type && link.id === record.record.id) return false;
  const fk = PARENT_FK[link.type];
  return Boolean(fk) && record.record[fk] === link.id;
}
