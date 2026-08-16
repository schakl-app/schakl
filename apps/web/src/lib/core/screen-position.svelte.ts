/**
 * Returning to a screen returns the visitor to *where they were on it* — the live half.
 *
 * The rules live in `screen-position.ts`, testable and browser-free. This is the state they act
 * on, the storage that outlives one navigation, and the two hooks that write and read it:
 *
 *  - `beforeNavigate` records the screen being left: its query string and the document's scroll
 *    offset. On the way out rather than on scroll, because a scroll listener is a per-frame cost
 *    on every screen for a value exactly one navigation ever reads, and leaving is the moment the
 *    answer is both final and needed.
 *  - `afterNavigate` restores the offset when the URL landed on is exactly the URL that was left
 *    (`restoreOffset`). The **back button needs none of this**: SvelteKit already restores scroll
 *    per history entry, so `popstate` is skipped and the two can never fight over one number.
 *
 * `returnQueries()` is what `breadcrumbsFor` reads to point a crumb at the slice the visitor left
 * (`/companies?page=3`) instead of at the section's front page. Only the crumb row, deliberately:
 * the **sidebar** is how you go to a section, and a nav item that quietly reapplied last hour's
 * filters would be a control that does not do what it says.
 *
 * It is keyed by pathname and every screen records — no registry of "screens this applies to",
 * which is a list somebody has to remember to add to, the failure the crumb row itself exists to
 * stop. So a record's tabbed detail page comes back to the tab it was on, and a long form comes
 * back to where it was being read, without either of them opting in.
 *
 * It lives in `sessionStorage`: the same lifetime as SvelteKit's own scroll restoration — this
 * tab, this visit — so it survives a reload of the detail page and dies with the tab.
 */
import { afterNavigate, beforeNavigate } from "$app/navigation";
import { tick } from "svelte";

import {
  parsePositions,
  remember,
  restoreOffset,
  returnQueriesOf,
  type ScreenPosition,
} from "$lib/core/screen-position";

/** Namespaced like the shell's other per-browser keys (`schakl:sidebar`, `schakl:navgroups`). */
const STORAGE_KEY = "schakl:screen-position";

let positions = $state<ScreenPosition[]>([]);
let loaded = false;

/** `pathname → query string` for every screen this visitor has a slice of. Read by the crumb row. */
export function returnQueries(): Record<string, string> {
  return returnQueriesOf(positions);
}

/**
 * The same answer for one path, for the handful of screens carrying their own "← Rapportages"
 * link above the crumb row. They are ordinary anchors to a static path, so without this they
 * return to page 1 of a list the visitor was on page 4 of — the very thing the crumb row no
 * longer does. A path never visited comes back unchanged, which is what the server renders.
 */
export function returnHref(path: string): string {
  return `${path}${positions.find((entry) => entry.path === path)?.search ?? ""}`;
}

function persist(): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(positions));
  } catch {
    // Quota, or a context that refuses storage (Safari private browsing throws). The in-memory
    // copy still serves this tab; only a reload forgets.
  }
}

/**
 * The two navigation hooks. Registered once, by the `(app)` layout during component init —
 * `beforeNavigate`/`afterNavigate` may only be called there. Never runs server-side, which is
 * also the honest answer for a first load: nobody has been anywhere yet.
 */
export function trackScreenPositions(): void {
  if (!loaded) {
    loaded = true;
    try {
      positions = parsePositions(sessionStorage.getItem(STORAGE_KEY));
    } catch {
      positions = [];
    }
  }

  beforeNavigate((nav) => {
    if (!nav.from) return;
    positions = remember(positions, nav.from.url.pathname, nav.from.url.search, window.scrollY);
    persist();
  });

  afterNavigate((nav) => {
    if (nav.type === "popstate" || !nav.to) return;
    const offset = restoreOffset(positions, nav.to.url);
    if (offset === null) return;
    void tick().then(() => {
      window.scrollTo(0, offset);
      // Once more a frame later: the first call can land before the browser has finished laying
      // the page out, and a document shorter than the offset asks for clamps the scroll to a
      // height that no longer applies by the time anyone sees it.
      requestAnimationFrame(() => window.scrollTo(0, offset));
    });
  });
}
