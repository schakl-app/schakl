/**
 * The two pieces the standalone error document interpolates values with.
 *
 * They live here rather than beside the renderer for one reason: the renderer imports Paraglide,
 * so it cannot be loaded by `node --test`, and these two are exactly the parts of it where being
 * wrong is a security bug rather than a typo. Everything they touch is attacker-reachable — the
 * requested path, and a brand name and logo URL a tenant typed into Huisstijl — on a page built
 * by string concatenation because it must render with no framework at all.
 *
 * Dependency-free on purpose, like `copy.ts` beside it.
 */

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** HTML-escape a value for text content *or* a quoted attribute — both appear on the page. */
export function esc(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/**
 * The retry link's target, or `null` when there is nothing safe to offer.
 *
 * The outage page renders on a `503` that *any* URL can produce, so the path it was asked for is
 * the one value on it an attacker chooses. Required to be site-relative: a page that reflected
 * `//evil.example` into its only link would be an open redirect served by the outage handler, and
 * a scheme-relative URL is exactly the shape that looks like a path and is not one.
 */
export function safeRetryHref(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  // A backslash is a path separator to some URL parsers and not to others, which is the whole
  // family of "/\evil.example" bypasses. Nothing we generate contains one.
  return value.includes("\\") ? null : value;
}
