/**
 * A branded error document that depends on nothing (Golden Rule 4, CLAUDE.md §7).
 *
 * `+error.svelte` is the error page for a running app: it has layout data, a hydrated client and
 * a stylesheet. This one is for the two moments where none of that is true —
 *
 *   1. the tenant fetch in `hooks.server.ts` failed, so no page can render at all, and
 *   2. Traefik could not reach the API and asked the SSR app for a page instead
 *      (`routes/edge-error/[status]`, `infra/traefik/dynamic.yml`).
 *
 * so it is one string: no JS, no CSS file, no font, no second request. Anything it needed to
 * fetch would be a thing that can also be down while it is trying to explain that something is
 * down. The tenant's colours and logo still land, because the branding is *cached* by the time
 * it is needed (`tenant-cache.server.ts`) — a maintenance page in a stranger's colours reads as
 * "you are on the wrong site", which is exactly the wrong message.
 *
 * The logo is the one remote thing on the page. It is served by the API, so during an API outage
 * it will not load — `onerror` is not available without JS, so the brand name is rendered beside
 * it rather than behind it, and an <img> that never arrives simply collapses.
 */
import { errorCopy } from "./copy";
import { esc, safeRetryHref } from "./markup";
import { tIn } from "../i18n";
import { DEFAULT_THEME, themeStyle, type OrgTheme } from "../theme";

export interface StandaloneErrorOptions {
  status: number;
  /** Last-known tenant branding; `DEFAULT_THEME` when nothing has been cached for this host. */
  theme?: OrgTheme;
  /** Resolved display locale — the cookie, else the tenant default. */
  locale?: string | null;
  /**
   * Where the one action link goes. Pass the path the visitor was actually on and a retryable
   * status turns the link into "probeer opnieuw"; leave it out and the link goes home, which is
   * the only honest offer when we do not know what they were asking for (the edge route is
   * handed a status and nothing else).
   */
  retryHref?: string;
}

/**
 * Render the document. Returns HTML; the caller decides the status and headers, because the
 * hook (503, `Retry-After`) and the edge route (whatever Traefik asked for) answer differently.
 */
export function renderStandaloneError(opts: StandaloneErrorOptions): string {
  const theme = opts.theme ?? DEFAULT_THEME;
  const locale = opts.locale || theme.defaultLocale || DEFAULT_THEME.defaultLocale;
  const copy = errorCopy(opts.status);

  const title = tIn(locale, copy.titleKey);
  const body = tIn(locale, copy.bodyKey);
  const code = tIn(locale, "errors.page.code", { status: opts.status });
  // "Probeer opnieuw" is a promise, so it is only made when the link really re-asks the failed
  // question: a retryable status *and* a path to re-ask it at. Otherwise the offer is "go
  // somewhere that works", which is true whatever happened.
  const retryHref = safeRetryHref(opts.retryHref);
  const retry = copy.retryable && !!retryHref;
  const href = esc(retry ? retryHref! : "/");
  const action = tIn(locale, retry ? "errors.page.retry" : "errors.page.home");

  const brand = theme.brandName ? esc(theme.brandName) : "";
  const logo = theme.logoUrl ? esc(theme.logoUrl) : "";

  return `<!doctype html>
<html lang="${esc(locale)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${esc(title)}</title>
<style>
/* The brand variables are declared in the sheet, never on <html style>: an inline style beats a
   media query, so stamping them the way the app shell does would have frozen a dark-mode
   visitor on the light-mode brand colour. */
:root{${themeStyle(theme, "light")}--bg:#fafafa;--surface:#fff;--text:#171717;--muted:#737373;--border:#e5e5e5}
@media (prefers-color-scheme: dark){:root{${themeStyle(theme, "dark")}--bg:#0a0a0a;--surface:#171717;--text:#fafafa;--muted:#a3a3a3;--border:#262626}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem;
background:var(--bg);color:var(--text);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{width:100%;max-width:26rem;background:var(--surface);border:1px solid var(--border);
border-radius:1rem;padding:2rem;text-align:center;box-shadow:0 1px 2px rgb(0 0 0 / .05)}
.brand{display:flex;align-items:center;justify-content:center;gap:.5rem;margin-bottom:1.5rem;
font-weight:600;font-size:.95rem;color:var(--text)}
.brand img{max-height:2rem;max-width:9rem;object-fit:contain}
h1{margin:0 0 .5rem;font-size:1.25rem;line-height:1.4;font-weight:600}
p{margin:0;font-size:.875rem;line-height:1.6;color:var(--muted)}
.code{margin-top:1.25rem;font-size:.75rem;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
a{display:inline-block;margin-top:1.5rem;font-size:.875rem;font-weight:500;
color:var(--brand-primary,#4f46e5);text-decoration:underline;text-underline-offset:.2em}
</style>
</head>
<body>
<main>
${logo || brand ? `<div class="brand">${logo ? `<img src="${logo}" alt="${brand}">` : ""}${!logo && brand ? brand : ""}</div>` : ""}
<h1>${esc(title)}</h1>
<p>${esc(body)}</p>
<a href="${href}">${esc(action)}</a>
<div class="code">${esc(code)}</div>
</main>
</body>
</html>
`;
}
