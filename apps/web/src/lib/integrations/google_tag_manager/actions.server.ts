/**
 * Form actions the Tag Manager connect surface posts to.
 *
 * Spread into `companies/[id]/+page.server.ts` beside `marketingActions`: a panel's control posts
 * to the *host* page's actions (docs/UX.md), which is what keeps the page's own reload the thing
 * that shows the result.
 *
 * Gating is the API's: `POST /gtm/containers` declares `google_tag_manager.settings.manage`, and
 * the connect surface mirrors that key rather than its own read one, or the one its five
 * neighbours in the picker use (#310).
 */
import { fail } from "@sveltejs/kit";
import type { RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

/**
 * The one write behind the connect control (#411), mirroring `marketingActions`' shape exactly.
 *
 * `companyId` is how the two callers differ and the only way they differ. On a client's page the
 * route *is* the client, so it is read from `event.params.id` and a posted value would be a
 * second answer free to disagree with it. Away from one — `/marketing` — the dialog asks, so the
 * form carries it.
 */
async function link(event: RequestEvent, companyId: string) {
  const form = await event.request.formData();
  const public_id = String(form.get("public_id") ?? "").trim();
  const company_id = companyId || String(form.get("company_id") ?? "").trim();
  if (!public_id || !company_id) return fail(400, { error: "errors.required" });
  const { error } = await apiFor(event).POST("/api/v1/gtm/containers", {
    body: { public_id, company_id },
  });
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { gtmLinked: true };
}

export const gtmActions = {
  gtmLink: (event: RequestEvent) => link(event, event.params.id as string),
};

/** Mounted by the pages that connect a container **without** a client in the route. */
export const gtmConnectActions = {
  gtmLink: (event: RequestEvent) => link(event, ""),
};
