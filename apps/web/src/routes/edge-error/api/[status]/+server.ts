/**
 * The **envelope** Traefik serves when the API is unreachable (infra/traefik/dynamic.yml).
 *
 * `/api/` and `/mcp` are routed straight to the API, so nothing renders a page there: the caller
 * is our own generated client, an MCP client or a browser `fetch`, and every one of them parses
 * `{ error: { code, message } }` where `message` is an i18n key (CLAUDE.md §9). Handing those
 * callers the HTML error page would be worse than handing them Traefik's plain-text 502 — a
 * screen would report "Er ging iets mis" over an unparseable body instead of "Deze omgeving is
 * even niet bereikbaar", and the browser console would fill with JSON parse errors.
 *
 * So the edge has two error middlewares, not one: this one for the API's paths, the sibling
 * route for everything else. Which body to send is a routing decision the edge already knows the
 * answer to — sniffing `Accept` here would be re-deriving it, badly.
 *
 * Like its sibling, it calls nothing and bypasses the tenant hook: it is asked for a body
 * precisely because a backend is not answering.
 */
import type { RequestHandler } from "./$types";

export const prerender = false;

export const GET: RequestHandler = ({ params }) => {
  const parsed = Number(params.status);
  const status = Number.isFinite(parsed) && parsed >= 400 && parsed <= 599 ? parsed : 502;
  return new Response(
    JSON.stringify({
      error: { code: "service_unavailable", message: "errors.page.unavailable.body" },
    }),
    {
      status,
      headers: {
        "content-type": "application/json",
        "retry-after": "15",
        "cache-control": "no-store",
      },
    },
  );
};

/**
 * Only `GET` — Traefik fetches an error page with `GET` whatever the failed request's method
 * was, and the body it gets back is what the original caller receives. So a `POST` to a dead API
 * still ends up with this envelope; it just is not this route that was asked for it.
 */
