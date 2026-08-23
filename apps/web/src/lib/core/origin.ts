/**
 * "Where did this detour start?" — the return address a panel link carries (#408).
 *
 * Opening a record from a client's page is a **detour**, not a destination: the visitor was
 * reading Bakkerij Van Loon, noticed a wrong phone number, went to fix it, and is finished the
 * moment it is saved. The app used to answer by leaving them standing on the contact — and, if
 * they pressed Verwijderen instead, by throwing them onto the org-wide address book, which is not
 * where they came from either. Every panel link was a bare path, so the client id was discarded at
 * the moment of the click and nothing downstream could get it back.
 *
 * Three mechanisms already in the tree look like they might answer and none of them does. The
 * **breadcrumb trail** (`breadcrumb-trail.svelte.ts`) infers the way in from navigation order —
 * the right design for a *crumb* and the wrong one for a *destination*: it is `sessionStorage` +
 * `afterNavigate`, so it is empty on a reload, empty in a new tab, and invisible to every
 * `+page.server.ts`, which is exactly where a delete's `redirect(303, …)` has to answer.
 * **`returnHref`** (`screen-position.svelte.ts`) restores the query string of a path you already
 * know and cannot tell you which path. And **`history.back()`** goes back one *navigation* rather
 * than one *task*: after a save that re-runs the load it can land on the form again, and on a
 * fresh tab it is silently a no-op.
 *
 * So the origin travels in the URL, which is the one place a reload, a new tab and the server can
 * all read it. The param name lives here and nowhere else, used by the producer and both readers,
 * for `edit-intent.ts`'s stated reason: so the two sides can never drift.
 *
 * **Absence keeps today's behaviour.** A record opened from its own register carries no `?from=`
 * and goes nowhere on save, which is what makes this landable without re-deciding every screen.
 *
 * The untrusted-string question is settled by `redirect.ts` and is deliberately not re-answered
 * here: a `?from=` travels in a URL anyone can write, so it is read through `safeInternalPath` —
 * the same whitelist-of-shape that keeps `?next=` from being an open redirect. Anything it refuses
 * is `null` and the caller falls back to its own default, never a 400: a stale link is not the
 * user's mistake.
 *
 * Kept free of `$app/*` so `tests/unit/origin.test.ts` can run it under node's bare test runner —
 * the reason `breadcrumb-labels.ts` exists beside `breadcrumbs.ts`, and the reason the import
 * below names its extension.
 */
import { safeInternalPath } from "./redirect.ts";

const ORIGIN_PARAM = "from";

/** Append `param=value` to a path that may already carry a query string. */
function append(path: string, value: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}${ORIGIN_PARAM}=${encodeURIComponent(value)}`;
}

/**
 * A link into a record, carrying where the visitor is standing now.
 *
 * `origin` is a whole `URL` rather than a pathname because the **search matters**: returning to
 * `/companies/<id>?tab=…` is returning to the screen they left, and `core/screen-position`
 * restores the scroll offset only for a URL it recognises as exactly that one. A nested `?from=`
 * on the origin rides along on purpose — a two-step detour returns through its middle.
 */
export function fromHref(path: string, origin: URL | string): string {
  const from = typeof origin === "string" ? origin : origin.pathname + origin.search;
  return safeInternalPath(from) ? append(path, from) : path;
}

/** Where a detour that started elsewhere should return to, or `null` — the caller's own default. */
export function originOf(url: URL): string | null {
  return safeInternalPath(url.searchParams.get(ORIGIN_PARAM));
}

/**
 * Carry this page's origin onto one of its own form actions.
 *
 * A browser resolves `?/delete` against the current URL, which **replaces** the whole query
 * string — so the origin would be dropped at the exact moment the server needs it, which is the
 * one case `sessionStorage` could never have served. SvelteKit reads the action from the first
 * parameter whose name starts with `/` and leaves the rest of the query alone.
 */
export function withOrigin(action: string, url: URL): string {
  const from = originOf(url);
  return from ? append(action, from) : action;
}
