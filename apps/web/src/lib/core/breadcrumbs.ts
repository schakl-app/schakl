/**
 * Breadcrumbs for every (app) page (owner request): one resolver, rendered once by the app
 * layout — no page opts in or out, so no page can forget them again. Crumbs derive from the
 * pathname; an `[id]` segment names itself from the record the page's own load already put
 * in `page.data` (company.name, invoice.number, …), never from an extra fetch.
 */
import { t } from "$lib/core/i18n";
import { settingsTitleKeys } from "$lib/core/settings-nav";

export interface Crumb {
  label: string;
  href?: string;
}

/** First path segment → label key. Static, so the resolver needs no registry at runtime. */
const ROOTS: Record<string, string> = {
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
const SETTINGS: Record<string, string> = {
  ...settingsTitleKeys(),
  subscriptions: "settings.subscriptions.title",
};

/** Known non-id tail segments. */
const TAILS: Record<string, string> = {
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
const TAILS_BY_ROOT: Record<string, Record<string, string>> = {
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
  marketing: {
    "google-ads": "nav.google_ads",
  },
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The loaded record's display name, tried against every detail-page data key in use. */
function entityLabel(data: Record<string, unknown>): string | null {
  const d = data as Record<string, Record<string, unknown> | undefined>;
  const named = d.company ?? d.project ?? d.domain ?? d.org ?? d.rule;
  if (named && typeof named.name === "string" && named.name) return named.name;
  if (d.contact) {
    const full = [d.contact.first_name, d.contact.last_name].filter(Boolean).join(" ");
    if (full) return full;
  }
  if (d.task && typeof d.task.title === "string" && d.task.title) return d.task.title;
  // A website has no name of its own — the host it answers on is the name, resolved exactly as
  // its page title and its list row do (`root` decides whether the `www.` prefix is part of it).
  if (d.website && typeof d.website.domain_name === "string" && d.website.domain_name) {
    return d.website.root ? d.website.domain_name : `www.${d.website.domain_name}`;
  }
  if (d.invoice) {
    return (d.invoice.number as string) || t("invoicing.status.draft");
  }
  if (d.quote) {
    return (d.quote.number as string) || t("invoicing.status.draft");
  }
  if (d.role) {
    const names = (d.role.name_i18n ?? {}) as Record<string, string>;
    return names.nl || names.en || (d.role.key as string) || null;
  }
  return null;
}

function prettify(segment: string): string {
  const clean = segment.replace(/-/g, " ");
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

export function breadcrumbsFor(pathname: string, data: Record<string, unknown>): Crumb[] {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return [{ label: t("nav.dashboard") }];

  const crumbs: Crumb[] = [];
  let href = "";
  segments.forEach((segment, i) => {
    href += `/${segment}`;
    let label: string;
    if (i === 0) {
      label = ROOTS[segment] ? t(ROOTS[segment]) : prettify(segment);
    } else if (segments[0] === "settings" && i === 1) {
      label = SETTINGS[segment] ? t(SETTINGS[segment]) : prettify(segment);
    } else if (UUID_RE.test(segment)) {
      label = entityLabel(data) ?? "…";
    } else {
      const key = TAILS_BY_ROOT[segments[0]]?.[segment] ?? TAILS[segment];
      label = key ? t(key) : prettify(segment);
    }
    crumbs.push({ label, href });
  });
  // The last crumb is the current page — it links nowhere.
  delete crumbs[crumbs.length - 1].href;
  return crumbs;
}
