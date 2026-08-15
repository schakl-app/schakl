/**
 * The page Traefik serves when Traefik itself produced the error (infra/traefik/dynamic.yml).
 *
 * The edge intercepts only `502-504` — the statuses it generates when a backend is unreachable —
 * and asks this route for a body. Anything the app produced itself already has a better page
 * (`+error.svelte`, or the hook's own 503), so intercepting more would replace good pages with
 * this one. Traefik keeps the original status code; the status in the path is what the page
 * should *say*.
 *
 * Two rules hold it up, and both are about not depending on the thing that is broken:
 *
 *   - **it never calls the API.** It is asked for a page at the exact moment something behind the
 *     edge is not answering, so branding comes from `tenant-cache.server.ts` or not at all. A
 *     blocking fetch here is how an error page becomes a hanging error page.
 *   - **it bypasses the tenant hook** (`hooks.server.ts`), for the same reason.
 *
 * Not reachable from outside: Traefik addresses the service directly rather than routing to it,
 * so nothing publishes this path. It is harmless if anyone finds it — a public branded page with
 * no tenant data on it, like the login screen's branding.
 */
import { renderStandaloneError } from "$lib/core/errors/standalone.server";
import { parseLocaleCookie } from "$lib/core/i18n";
import { lastKnownTheme } from "$lib/core/tenant-cache.server";

import type { RequestHandler } from "./$types";

export const prerender = false;

export const GET: RequestHandler = ({ params, request }) => {
  // The segment arrives from the edge's own `{status}` template, so it is ours — but it is still
  // a URL segment, and `errorCopy` answers generically for anything it does not recognise.
  const status = Number(params.status);
  const html = renderStandaloneError({
    status: Number.isFinite(status) ? status : 502,
    // Traefik forwards the original request, so this is the browser's host — the key the tenant
    // was cached under on the last request that worked.
    theme: lastKnownTheme(request.headers.get("host")) ?? undefined,
    locale: parseLocaleCookie(request.headers.get("cookie")),
  });
  return new Response(html, {
    status: Number.isFinite(status) ? status : 502,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "retry-after": "15",
      "cache-control": "no-store",
    },
  });
};
