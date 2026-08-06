import { error as httpError, json } from "@sveltejs/kit";

import { apiFor } from "$lib/core/session";

import type { RequestEvent } from "./$types";

/**
 * The payment QR as an unsaved config would draw it (#305) — the editor's colour picker.
 *
 * Its own proxy beside `preview/` because it answers a different question at a different
 * speed: the document preview is a full render and is debounced at 400ms, this is one encode
 * and can keep up with dragging a swatch. It also carries the one thing the document preview
 * cannot show at 3cm — whether `readable_pair` substituted the colours — which is what lets
 * the editor explain the rule instead of appearing to ignore what was typed.
 *
 * JSON rather than an `image/svg+xml` response: the `replaced` flag travels with the markup,
 * and the SVG is inlined into the page anyway (the API's own document does the same, for the
 * same CSP reason).
 */
export const POST = async (event: RequestEvent) => {
  const body = await event.request.json();
  const { data, error, response } = await apiFor(event).POST(
    "/api/v1/invoicing/templates/qr-preview",
    { body },
  );
  if (error || !data) throw httpError(response?.status ?? 500);
  return json(data, { headers: { "cache-control": "no-store" } });
};
