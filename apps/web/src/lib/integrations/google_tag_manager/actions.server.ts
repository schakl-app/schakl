/**
 * Company-page form actions the Tag Manager panel posts to.
 *
 * Spread into `companies/[id]/+page.server.ts` beside `marketingActions`: a panel's control posts
 * to the *host* page's actions (docs/UX.md), which is what keeps the page's own reload the thing
 * that shows the result.
 *
 * The client is the route, so it is read from `event.params.id` and never from the form — a
 * posted client id would be a second answer free to disagree with the page it was posted from.
 * Gating is the API's: `POST /gtm/containers` declares `google_tag_manager.settings.manage`, and
 * the panel mirrors that key rather than its own read one (#310).
 */
import { fail } from "@sveltejs/kit";
import type { RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export const gtmActions = {
  gtmLink: async (event: RequestEvent) => {
    const public_id = String((await event.request.formData()).get("public_id") ?? "").trim();
    if (!public_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/gtm/containers", {
      body: { public_id, company_id: event.params.id as string },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { gtmLinked: true };
  },
};
