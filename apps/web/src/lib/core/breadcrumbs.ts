/**
 * Breadcrumbs for every (app) page (owner request): one resolver, rendered once by the app
 * layout — no page opts in or out, so no page can forget them again. Crumbs derive from the
 * pathname; an `[id]` segment names itself from the record the page's own load already put
 * in `page.data` (company.name, invoice.number, …), never from an extra fetch.
 *
 * What a segment is *called* lives in `breadcrumb-labels.ts`, so a test can sweep the route tree
 * against it. This file is the rendering: it resolves those keys, prefers the tenant's own nav
 * label over the declared one, and splices in the way the visitor actually came.
 *
 * **A trail may follow the way in, but only over a link the record itself confirms.** See
 * `breadcrumb-trail.svelte.ts`: the visitor's route through the app *suggests* the ancestors, and
 * the record's own foreign keys are what decide whether they are drawn. A breadcrumb built from
 * history alone is a back button claiming to be a hierarchy, and it lies the first time somebody
 * opens a second record in a new tab.
 */
import {
  literalLabelKey,
  pageRecord,
  routeParamNames,
  UUID_RE,
  type CrumbLink,
} from "$lib/core/breadcrumb-labels";
import { t } from "$lib/core/i18n";
import type { NavItem } from "$lib/core/registry";

export type { CrumbLink, PageRecord } from "$lib/core/breadcrumb-labels";
export {
  isParentOf,
  MAX_ANCESTORS,
  pageRecord,
  routeParamNames,
} from "$lib/core/breadcrumb-labels";

export interface Crumb {
  label: string;
  href?: string;
}

function prettify(segment: string): string {
  const clean = segment.replace(/-/g, " ");
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

/** The tenant's own word for a section, when the sidebar contributes an item for exactly this href. */
function navLabel(href: string, nav: readonly NavItem[]): string | null {
  const item = nav.find((entry) => entry.href === href);
  const label = item?.label();
  return typeof label === "string" && label.trim() ? label : null;
}

/** One literal segment: the sidebar's word for it, else its declared key, else the bare slug. */
function labelFor(
  segments: string[],
  index: number,
  href: string,
  nav: readonly NavItem[],
): string {
  const key = literalLabelKey(segments, index);
  return navLabel(href, nav) ?? (key ? t(key) : prettify(segments[index]));
}

export interface CrumbInput {
  pathname: string;
  /** `page.route.id` — what says which segments are parameters. */
  routeId?: string | null;
  data: Record<string, unknown>;
  /** The viewer's nav items, so a section crumb reads whatever the sidebar was renamed to (#169). */
  nav?: readonly NavItem[];
  /** Ancestors the visitor came through, already confirmed against this page's own record. */
  trail?: readonly CrumbLink[];
  /**
   * `pathname → query string` for the screens this visitor has already been on
   * (`core/screen-position`). A crumb whose path is in here links to the slice they left — page 3
   * of the filtered list — rather than to the section's front page. See `withReturnQueries`.
   */
  returnQueries?: Readonly<Record<string, string>>;
}

export function breadcrumbsFor(input: CrumbInput): Crumb[] {
  const { pathname, routeId, data, nav = [], trail = [], returnQueries = {} } = input;
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return [{ label: t("nav.dashboard") }];

  const params = routeParamNames(routeId);
  const record = pageRecord(data, t);
  const dynamic = segments.map((segment, i) =>
    typeof params[i] === "string" ? true : params[i] === undefined && UUID_RE.test(segment),
  );

  const crumbs: Crumb[] = [];
  let href = "";
  segments.forEach((segment, i) => {
    href += `/${segment}`;
    crumbs.push({
      label: dynamic[i] ? (record?.label ?? "…") : labelFor(segments, i, href, nav),
      href,
    });
  });

  const withTrail = spliceTrail(crumbs, dynamic, trail, nav);
  // The last crumb is the current page — it links nowhere.
  delete withTrail[withTrail.length - 1].href;
  return withReturnQueries(withTrail, returnQueries);
}

/**
 * Point each crumb at the visitor's own view of that screen.
 *
 * A crumb href is derived from the path, so it names the section and nothing else: click
 * "Bedrijven" from the fiftieth client and you get page 1 of an unfiltered list, which is not the
 * screen you were on a click ago and not the screen the crumb claims to go back to. Where
 * `core/screen-position` has a query string for that exact pathname, it is the one they left, so
 * it is the one the crumb restores.
 *
 * The last crumb is the current page and already has no href; a `?` is never appended twice
 * because the derived hrefs are pure paths.
 */
function withReturnQueries(
  crumbs: Crumb[],
  returnQueries: Readonly<Record<string, string>>,
): Crumb[] {
  return crumbs.map((crumb) => {
    const search = crumb.href ? returnQueries[crumb.href] : undefined;
    return search ? { ...crumb, href: `${crumb.href}${search}` } : crumb;
  });
}

/**
 * Replace the section prefix with the route the visitor actually took.
 *
 * `Projecten › Site herbouw` becomes `Bedrijven › Acme › Site herbouw` when the project was opened
 * from that client's page: the section root is dropped because it is not how they got here, and
 * anything *below* the record (`…/print`) is kept because the trail says nothing about it. With no
 * confirmed ancestors, or on a page about no record at all, the path-derived row stands — which is
 * what every server-rendered first load and every shared link gets.
 */
function spliceTrail(
  crumbs: Crumb[],
  dynamic: boolean[],
  trail: readonly CrumbLink[],
  nav: readonly NavItem[],
): Crumb[] {
  if (trail.length === 0) return crumbs;
  const recordIndex = dynamic.lastIndexOf(true);
  if (recordIndex < 0) return crumbs;
  const rootSegment = trail[0].href.split("/").filter(Boolean)[0];
  if (!rootSegment) return crumbs;
  const root: Crumb = {
    label: labelFor([rootSegment], 0, `/${rootSegment}`, nav),
    href: `/${rootSegment}`,
  };
  return [
    root,
    ...trail.map((link) => ({ label: link.label, href: link.href })),
    ...crumbs.slice(recordIndex),
  ];
}
