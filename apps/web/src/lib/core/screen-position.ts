/**
 * Where the visitor was on a screen — the bookkeeping half, kept free of runes and of the browser
 * so a test can sweep it. The state, the storage and the navigation hooks are in
 * `screen-position.svelte.ts`; the rules that decide what is remembered and what is restored are
 * here, because every one of them is invisible in a screenshot and obvious in an assertion.
 *
 * The problem it solves: open the clients list, page to 3, scroll to the fiftieth row, click it,
 * then click "Bedrijven" in the crumb row. You land on page 1, at the top — a different screen
 * from the one you were on a click ago, and not the one the crumb claims to return to. Both halves
 * are the same mistake. **A link back to a screen names the screen, and `/companies` is not
 * `/companies?page=3`.**
 */

/** How many screens are remembered at once. Most-recently-left first; the tail is dropped. */
export const MAX_POSITIONS = 40;

export interface ScreenPosition {
  /** The pathname this belongs to — the key. `/companies`, `/companies/<uuid>`. */
  path: string;
  /** The query string the visitor had there, leading `?` included. `""` when there was none. */
  search: string;
  /** Document scroll offset in CSS pixels, at the moment they left. */
  y: number;
}

function isPosition(value: unknown): value is ScreenPosition {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.path === "string" &&
    typeof entry.search === "string" &&
    typeof entry.y === "number" &&
    Number.isFinite(entry.y)
  );
}

/**
 * What a tab already knows, out of the string `sessionStorage` handed back. Anything unparseable,
 * of the wrong shape, or written by an older release degrades to "nothing remembered" — the worst
 * outcome of that is a crumb behaving exactly as it did before any of this existed, which is not
 * worth throwing a navigation for.
 */
export function parsePositions(raw: string | null): ScreenPosition[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isPosition).slice(0, MAX_POSITIONS);
  } catch {
    return [];
  }
}

/**
 * Record a screen, moving it to the front — one entry per pathname, so the *last* view of a list
 * is the one a crumb returns to. A visitor who pages 3 → 5 and then opens a record must come back
 * to 5; keeping both would make "which one" a question with no answer.
 */
export function remember(
  positions: readonly ScreenPosition[],
  path: string,
  search: string,
  y: number,
): ScreenPosition[] {
  const offset = Number.isFinite(y) ? Math.max(0, Math.round(y)) : 0;
  return [{ path, search, y: offset }, ...positions.filter((entry) => entry.path !== path)].slice(
    0,
    MAX_POSITIONS,
  );
}

/**
 * `pathname → query string`, for the crumb row. Only screens that *had* a query appear: a bare
 * path needs no help, and an empty entry would only make the caller test for one.
 */
export function returnQueriesOf(positions: readonly ScreenPosition[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const entry of positions) {
    if (entry.search) map[entry.path] = entry.search;
  }
  return map;
}

/**
 * How far to scroll on arriving at `url`, or `null` for "leave it alone".
 *
 * The rule is **exact URL match**, and it is the whole safety property. A crumb carrying
 * `?page=3` asks for the screen that was left and gets its offset back; a sidebar click on a bare
 * `/companies` asks for the section and gets the top of it, because the section's front page is
 * not the slice anybody left. It also means a filter change, a page step and a fresh search all
 * land at the top by construction rather than by each list remembering to say so.
 *
 * A zero offset is `null` rather than `0` for the same reason: scrolling to the top is what the
 * browser was going to do anyway, and asking for it would only add a frame in which it might not.
 */
export function restoreOffset(
  positions: readonly ScreenPosition[],
  url: { pathname: string; search: string },
): number | null {
  const saved = positions.find((entry) => entry.path === url.pathname);
  if (!saved || saved.search !== url.search || saved.y <= 0) return null;
  return saved.y;
}
