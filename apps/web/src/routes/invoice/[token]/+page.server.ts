import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The invoice a client opens from a link, with no login (#304).
 *
 * It lives at the route tree's **top level**, outside `(app)`, and that is the whole guard:
 * `(app)/+layout.server.ts` redirects an anonymous visitor to `/login`, so a public page nested
 * under it could only work by punching a hole in the one place that protects every other
 * screen. Here there is no hole — there is simply nothing above this route that asks who you
 * are. The tenant theme still resolves (`hooks.server.ts` runs on every request), so the page
 * is branded like the rest of the app without knowing anything about a session.
 *
 * Everything it can do, it does through the API's public surface, which authenticates the
 * token itself. The web app holds no opinion about what the token grants — it cannot: it never
 * sees an invoice id, only the string in its own URL.
 */
export const load: PageServerLoad = async (event) => {
  const token = event.params.token;
  const api = apiFor(event);

  /**
   * The one thing this load does beyond fetching: when the payer is coming *back* from a
   * checkout (`?return=1`, stamped by the API when it created the intent), ask the provider
   * once before rendering.
   *
   * Server-side rather than in `onMount`, so it works with no JavaScript at all and so the
   * first paint is usually already correct — a provider webhook is asynchronous and routinely
   * lands after the browser redirect, which is exactly why the page used to greet someone who
   * had just paid with the word "open". The API bounds the call (non-final attempts only, one
   * per attempt per five seconds), so a reloaded return URL costs nothing.
   */
  if (event.url.searchParams.get("return") === "1") {
    await api.POST("/api/v1/invoicing/public/invoices/{token}/refresh", {
      params: { path: { token } },
    });
  }

  const { data } = await api.GET("/api/v1/invoicing/public/invoices/{token}", {
    params: { path: { token } },
  });
  // Every refusal the API makes is a 404 — unknown token, a draft, a tenant that switched
  // public links off — and this passes that through unexamined. Distinguishing them on screen
  // would tell someone trying strings which guess was closer.
  if (!data) throw httpError(404);

  return {
    token,
    invoice: data,
    /** Set only on the hop back from a provider, so the page knows to poll rather than sit. */
    returning: event.url.searchParams.get("return") === "1",
  };
};

export const actions: Actions = {
  /**
   * Open a checkout and send the payer to it.
   *
   * A form action rather than a `fetch`, and it answers with a **303**, so the button works
   * with no JavaScript at all: this page is opened by scanning a QR with whatever browser was
   * behind a phone camera, and a pay button that needs hydration is a pay button that
   * sometimes does not work.
   *
   * The location is off-site, which SvelteKit's client-side `enhance` cannot follow on its own
   * (`goto` refuses an external URL) — so the page passes a handler that assigns
   * `window.location` for `type: "redirect"`. Both paths end in the same place; only the plumbing
   * differs.
   *
   * The body carries nothing at all — no amount, no account — because the API decides both, and
   * a public endpoint that accepted either would be a public endpoint deciding what somebody
   * owes.
   */
  pay: async (event) => {
    const { data, error } = await apiFor(event).POST(
      "/api/v1/invoicing/public/invoices/{token}/payment-intents",
      { params: { path: { token: event.params.token } } },
    );
    if (error || !data?.checkout_url) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, data.checkout_url);
  },
};
