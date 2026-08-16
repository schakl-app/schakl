/**
 * The form action behind the Gmail "scan mailbox" button (#341).
 *
 * A host list page spreads this into its `actions`, the same contract `driveActions` uses —
 * the button lives on the interactions timeline, but what it drives is a Google connection,
 * so the call belongs to this module rather than to whichever screen renders it.
 *
 * A cooldown is not a failure: the API answers 200 with `status: "cooldown"` and the seconds
 * left, and returning it as data rather than as `fail()` is what lets the button keep showing
 * when the feed was last refreshed instead of collapsing into an error line.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import type { components } from "$lib/core/api/schema";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export type GmailRefreshResult = components["schemas"]["GmailRefreshResult"];

export const gmailActions = {
  refreshGmail: async (event: RequestEvent) => {
    const { data, error } = await apiFor(event).POST("/api/v1/google/gmail/refresh", {});
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { gmailRefresh: data as GmailRefreshResult };
  },
};
