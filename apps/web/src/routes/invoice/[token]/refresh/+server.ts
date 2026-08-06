import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * "Has my payment landed yet?", for the page to poll after a return from a checkout (#304).
 *
 * A tiny endpoint rather than a form action because the *page* is what re-reads: the poll
 * fires, this answers, and `invalidateAll()` redraws from the load. Modelling it as an action
 * would have made every poll a form submission with a navigation attached.
 *
 * It is deliberately unbounded here and bounded at the API, where the bound belongs: the
 * service asks the provider at most once per attempt per five seconds, and only about attempts
 * that are still in flight. A limit written in the browser is a limit an attacker skips.
 *
 * Failures are swallowed into `{ ok: false }`. The caller is a background poll on a page the
 * client is reading; a 500 toast because a provider was briefly unreachable would be noise
 * about something that fixes itself on the next tick, and the reconcile cron is underneath it.
 */
export const POST = async (event: RequestEvent) => {
  const { data } = await apiFor(event).POST("/api/v1/invoicing/public/invoices/{token}/refresh", {
    params: { path: { token: event.params.token } },
  });
  return json({ ok: Boolean(data), status: data?.invoice_status ?? null });
};
