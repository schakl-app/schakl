import { json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * "Has my payment landed yet?" for a **signed-in** payer — the portal's half of #304.
 *
 * The mirror of `/invoice/[token]/refresh`, and it exists for the same reason: a provider's
 * webhook is asynchronous and routinely arrives after the browser redirect, so the page a payer
 * returns to had already read the invoice before anything told us. A client with a portal login
 * hit exactly the bug a client with a link did — and had even less recourse, because the only
 * control that could fix it (`sync`) is `:any`, which a client never holds.
 *
 * `invoicing.payment.link` at the floor, so the client's own `:own` grant satisfies it. The
 * bound is the API's: non-final attempts only, one provider call per attempt per five seconds.
 */
export const POST = async (event: RequestEvent) => {
  const { data } = await apiFor(event).POST(
    "/api/v1/invoicing/invoices/{invoice_id}/payment-intents/refresh",
    { params: { path: { invoice_id: event.params.id } } },
  );
  return json({ ok: Boolean(data), status: data?.invoice_status ?? null });
};
