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
import { originOf } from "./origin.ts";
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
  timeon: "nav.timeon",
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
  leave: {
    availability: "leave.availability.title",
  },
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
    "tag-manager": "nav.gtm",
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

/**
 * Every record type the crumb row can be about. Exported for the sweep in
 * `tests/unit/breadcrumbs.test.ts`, which asks of each one whether it can confirm a client — a
 * question a new detail page has to answer rather than fail silently (#401).
 */
export const RECORD_TYPES: string[] = RECORDS.map((spec) => spec.type);

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
 * How a record names an ancestor of a given type: a **column** it carries, or a **collection** of
 * rows each carrying that column. Either way it is the record's own data that decides, which is
 * the whole of the "is the way in true?" check — an ancestor the record does not name is never
 * drawn.
 *
 * `name` is where the record *calls* it, and it exists for the stated ancestor below: a trail
 * inferred from navigation order carries the previous page's own label, while a `?from=` carries
 * only a path — and the label is display text, so it may never be taken from the URL. A record
 * that confirms an ancestor it cannot name simply draws no crumb for it.
 */
export type ParentRule =
  { fk: string; name?: string } | { collection: string; fk: string; name: string };

/**
 * The rules per ancestor type, tried in order.
 *
 * `company` carries two because a client link is not always a column (#401). A contact belongs to
 * its clients through `company_contacts`, so `ContactRead` answers with a *list* and there is no
 * `company_id` to read — which meant a contact opened from a client's page confirmed nothing, the
 * trail reset, and "up" became the org-wide address book. Every other record the crumb row can be
 * about does carry a scalar `company_id` (a website's is its domain's, resolved by the API), and
 * `tests/unit/breadcrumbs.test.ts` checks that against the generated schema rather than trusting
 * this comment: CLAUDE.md §15's "failure mode (1) — no anchor" is the same shape one layer out,
 * and the models that hit it declare `__company_horizon_clause__` for the same reason.
 */
const PARENT_RULES: Record<string, ParentRule[]> = {
  company: [
    { fk: "company_id", name: "company_name" },
    { collection: "companies", fk: "company_id", name: "name" },
  ],
  project: [{ fk: "project_id", name: "project_name" }],
  domain: [{ fk: "domain_id", name: "domain_name" }],
  contact: [{ fk: "contact_id", name: "contact_name" }],
  task: [{ fk: "task_id", name: "task_title" }],
};

/** The rules that could confirm an ancestor of this type — read by the schema sweep. */
export function parentRules(type: string): ParentRule[] {
  return PARENT_RULES[type] ?? [];
}

function names(rule: ParentRule, fields: Fields, id: string): boolean {
  if ("collection" in rule) {
    const rows = fields[rule.collection];
    return (
      Array.isArray(rows) &&
      rows.some((row) => row !== null && typeof row === "object" && (row as Fields)[rule.fk] === id)
    );
  }
  return fields[rule.fk] === id;
}

export function isParentOf(link: CrumbLink, record: PageRecord): boolean {
  // A tab of a record is not a child of it: `/companies/<id>/reporting` is still about that
  // company, and without this the row would name it twice in a row.
  if (link.type === record.type && link.id === record.record.id) return false;
  return parentRules(link.type).some((rule) => names(rule, record.record, link.id));
}

/**
 * First path segment → the record type that section addresses, for reading an ancestor **stated**
 * in a `?from=` (#408) rather than inferred from navigation order.
 *
 * Only the sections a detour can start from are listed. A path naming anything else is not a
 * hierarchy claim we know how to check, so it returns no crumb and keeps working perfectly well
 * as a return destination — the two questions are separate, and only this one needs confirming.
 */
export const ANCESTOR_ROUTES: Record<string, string> = {
  companies: "company",
  projects: "project",
  domains: "domain",
  contacts: "contact",
  tasks: "task",
};

/** What a page already loaded calls a record of some type: the crumb label of last resort. */
export type AncestorLookup = (type: string, id: string) => string | null;

/** How this record names the ancestor `link`, through whichever rule confirmed it. */
function labelFrom(link: CrumbLink, record: PageRecord): string | null {
  for (const rule of parentRules(link.type)) {
    if (!names(rule, record.record, link.id)) continue;
    if (!("collection" in rule)) {
      return rule.name ? str(record.record[rule.name]) : null;
    }
    const rows = record.record[rule.collection] as Fields[];
    const row = rows.find((entry) => entry[rule.fk] === link.id);
    if (row) return str(row[rule.name]);
  }
  return null;
}

/**
 * The ancestor a `?from=` *states*, or `null`.
 *
 * **#401 makes an inferred parent confirmable; this makes a stated one authoritative** — and the
 * safety property is unchanged either way: the record has to name the ancestor before it is drawn,
 * or a hand-written URL would become a hierarchy. What the URL adds is the three cases the
 * navigation-order trail can never serve, because it lives in `sessionStorage`: a reload, a new
 * tab, and the first render after a server-side redirect.
 *
 * `lookup` is the fallback for a record that confirms its ancestor and carries no name for it — a
 * task names its client with `company_id` and nothing else — answered from the lists the page's
 * own load already holds rather than from an extra fetch.
 */
export function statedAncestor(
  url: URL,
  record: PageRecord,
  lookup?: AncestorLookup,
): CrumbLink | null {
  const from = originOf(url);
  if (!from) return null;
  const segments = from.split(/[?#]/)[0].split("/").filter(Boolean);
  const type = ANCESTOR_ROUTES[segments[0]];
  if (!type || segments.length < 2) return null;
  const link: CrumbLink = {
    type,
    id: segments[1],
    label: "",
    href: `/${segments[0]}/${segments[1]}`,
  };
  if (!isParentOf(link, record)) return null;
  const label = labelFrom(link, record) ?? lookup?.(type, link.id) ?? null;
  return label ? { ...link, label } : null;
}
