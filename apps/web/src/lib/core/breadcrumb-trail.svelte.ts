/**
 * The route the visitor actually took, kept only as far as the records confirm it.
 *
 * The app is a graph, not a tree: a project hangs off a client, a task off a project, and every
 * one of them is also reachable from its own list in the sidebar. So a path-derived crumb row is
 * right about *where you are* and silent about *how you got here* — open a project from Acme's
 * page and the row said "Projecten › Site herbouw", with no way back to the client whose page you
 * were reading a click ago.
 *
 * This remembers the previous page's record and offers it to the next one as a candidate ancestor.
 * The candidate is drawn **only if the new record names it** (`project.company_id === Acme.id`) —
 * history suggests, the record decides. That is the whole safety property, and it is what keeps
 * this from becoming a back button dressed as a hierarchy: a trail assembled from visit order
 * alone starts lying the moment somebody opens a record in a new tab, follows a notification link,
 * or walks two unrelated screens in a row.
 *
 * Three consequences worth stating:
 *  - It is **browser-only**. `afterNavigate` never runs server-side, so a first load, a reload and
 *    a shared link all render the plain path-derived row. That is the honest answer: nobody came
 *    from anywhere.
 *  - It **walks back**, rather than resetting, when the immediately previous record is not a
 *    parent: leaving a task for one of its client's invoices keeps `Bedrijven › Acme`, because the
 *    invoice names Acme even though it has nothing to do with the task.
 *  - It is **capped** at `MAX_ANCESTORS`. The chains that exist today are two deep; the cap is what
 *    stops a future one from growing a crumb row nobody can read.
 */
import { afterNavigate } from "$app/navigation";
import { page } from "$app/state";

import {
  isParentOf,
  MAX_ANCESTORS,
  pageRecord,
  routeParamNames,
  type CrumbLink,
  type PageRecord,
} from "$lib/core/breadcrumb-labels";
import { t } from "$lib/core/i18n";

let trail = $state<CrumbLink[]>([]);
/** The page we are about to leave: its own confirmed ancestors, plus the record it was about. */
let previous: { trail: CrumbLink[]; self: CrumbLink | null } | null = null;

/** The confirmed ancestors of the page currently on screen. Empty until proven otherwise. */
export function crumbTrail(): CrumbLink[] {
  return trail;
}

/** Registered once, by the (app) layout, during component init — `afterNavigate` requires that. */
export function trackCrumbTrail(): void {
  afterNavigate(() => {
    const record = pageRecord(page.data as Record<string, unknown>, t);
    const candidates = previous
      ? [...previous.trail, ...(previous.self ? [previous.self] : [])]
      : [];
    trail = record ? confirmedAncestors(candidates, record) : [];
    previous = { trail, self: selfLink(record) };
  });
}

/**
 * The longest prefix of the candidates that ends in a genuine parent of this record. Walking back
 * rather than testing only the last one is what survives a sideways step between two records that
 * share a client.
 */
function confirmedAncestors(candidates: CrumbLink[], record: PageRecord): CrumbLink[] {
  for (let depth = candidates.length; depth > 0; depth--) {
    if (isParentOf(candidates[depth - 1], record)) {
      return candidates.slice(0, depth).slice(-MAX_ANCESTORS);
    }
  }
  return [];
}

/** This page as an ancestor of the next one: its record, addressed at its own URL. */
function selfLink(record: PageRecord | null): CrumbLink | null {
  if (!record) return null;
  const segments = page.url.pathname.split("/").filter(Boolean);
  const params = routeParamNames(page.route.id);
  // A tab of a record (`/companies/<id>/reporting`) is still that record, so the link points at
  // the parameter itself rather than at whatever tail the visitor happened to be on.
  let index = -1;
  for (let i = 0; i < segments.length; i++) {
    if (typeof params[i] === "string") index = i;
  }
  if (index < 0) return null;
  return {
    type: record.type,
    id: String(record.record.id),
    label: record.label,
    href: `/${segments.slice(0, index + 1).join("/")}`,
  };
}
