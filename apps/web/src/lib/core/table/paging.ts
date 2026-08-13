/**
 * One pagination contract for every list (docs/PERFORMANCE.md, docs/UX.md).
 *
 * The rule this replaces was "ask for 200 rows and hope": a list that outgrew the cap silently
 * became a *sample* of itself, and the only way to reach row 201 was to guess a search term
 * narrow enough. Worse, the truncation looked exactly like the whole answer — the same failure
 * shape as a truncated total (#37), one layer up.
 *
 * Three properties hold it up.
 *
 * **The URL is the view.** `?page=` and `?size=` fully describe which slice is on screen, so the
 * back button lands where the user left, a page is shareable, and SvelteKit restores the scroll
 * position for free. That is also why every filter, search and sort control must drop `page`
 * (`resetPage`) — page 7 of the old filter is not page 7 of the new one.
 *
 * **The preference is the default, never the state.** `TablePref.page_size` is what the list
 * opens on; the URL wins whenever it speaks. Storing the *current* page in the preference
 * instead would make two tabs fight over one number and break the back button outright.
 *
 * **The size is bounded by what the API will serve.** Every list endpoint caps `limit` at 200
 * (`le=200`), so an out-of-range `?size=` is clamped here rather than 422-ing the load.
 */
import type { TablePref } from "./columns";

/** What the size selector offers. Ordered, and every value ≤ `MAX_PAGE_SIZE`. */
export const PAGE_SIZES = [25, 50, 100, 200] as const;

/** What a list shows when the user has never chosen: a screenful, not a database dump. */
export const DEFAULT_PAGE_SIZE = 50;

/** The API's own cap (`Query(..., le=200)` on every list route). Asking for more is a 422. */
export const MAX_PAGE_SIZE = 200;

export interface Paging {
  /** 1-based, straight from the URL. What the user sees and links to. */
  page: number;
  /** Rows per page — the API's `limit`. */
  limit: number;
  /** Derived from the two above. The API's `offset`. */
  offset: number;
}

/** Clamp anything at all into a servable page size. Junk degrades to the fallback, never throws. */
export function coercePageSize(raw: unknown, fallback: number = DEFAULT_PAGE_SIZE): number {
  const parsed = typeof raw === "number" ? raw : Number(String(raw ?? "").trim());
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return Math.min(Math.floor(parsed), MAX_PAGE_SIZE);
}

/**
 * The slice this request asks for: URL first, saved preference second, the list's own default
 * last. Call it in the `+page.server.ts` load and hand `limit`/`offset` straight to the API.
 */
export function resolvePaging(
  url: URL,
  pref: TablePref = {},
  fallback: number = DEFAULT_PAGE_SIZE,
): Paging {
  const preferred = coercePageSize(pref.page_size, fallback);
  const limit = url.searchParams.has("size")
    ? coercePageSize(url.searchParams.get("size"), preferred)
    : preferred;

  const raw = Number(url.searchParams.get("page") ?? 1);
  const page = Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;

  return { page, limit, offset: (page - 1) * limit };
}

/** How many pages a total spans. Always at least one, so "page 1 of 0" is unsayable. */
export function pageCount(total: number, limit: number): number {
  if (limit <= 0) return 1;
  return Math.max(1, Math.ceil(total / limit));
}

/**
 * Whether the *stepping* controls have anywhere to go (#334).
 *
 * The pager's frame — the range and the size selector — is unconditional: "twelve results" is as
 * much of an answer as "51–100 of 812", and it is the one the reader only ever gets on the short
 * lists. Hiding the whole bar below one page is what made seven list screens print their own
 * total under the heading, in four different wordings, saying it twice on a long one.
 *
 * Arrows and numbered chips are the part that may stand down: over a single page they can never
 * act, and a lone highlighted "1" states nothing the range has not. `page > 1` is not redundant —
 * a hand-typed `?page=5` over nine rows still needs its way back.
 */
export function hasPageSteps(page: number, pages: number): boolean {
  return pages > 1 || page > 1;
}

/**
 * Drop `page` from a URL a filter/search/sort control is about to navigate to. Every such
 * control must call this: keeping the page number across a filter change strands the user on an
 * empty page of a shorter list, which reads as "the filter found nothing".
 */
export function resetPage(url: URL): URL {
  url.searchParams.delete("page");
  return url;
}

/** The href for a given page, keeping every other parameter. Page 1 is the bare URL. */
export function pageHref(url: URL, page: number): string {
  const next = new URL(url);
  if (page <= 1) next.searchParams.delete("page");
  else next.searchParams.set("page", String(page));
  return `${next.pathname}${next.search}`;
}

/**
 * The numbered buttons: first page, last page, and a window around the current one, with `null`
 * where a run was elided. A 40-page list must not render 40 links on a phone.
 */
export function pageWindow(page: number, count: number, span = 1): (number | null)[] {
  if (count <= 1) return [1];

  const wanted = new Set<number>([1, count]);
  for (let p = page - span; p <= page + span; p++) {
    if (p >= 1 && p <= count) wanted.add(p);
  }

  const out: (number | null)[] = [];
  let previous = 0;
  for (const p of [...wanted].sort((a, b) => a - b)) {
    // A gap of exactly one is spelled out — "1 … 3" is longer and less useful than "1 2 3".
    if (previous && p - previous === 2) out.push(previous + 1);
    else if (previous && p - previous > 2) out.push(null);
    out.push(p);
    previous = p;
  }
  return out;
}
