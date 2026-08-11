import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { GOOGLE_ADS_DECISIONS_TABLE_ID } from "$lib/modules/google_ads/columns";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The decisions log — a list screen, so it pages (CLAUDE.md §9).
 *
 * `?page=`/`?size=` in the URL, resolved here and applied by the API. A `limit: 200, offset: 0`
 * would make a tenant who has been reviewing search terms for a year read a sample of their own
 * history as the whole of it.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_ads.account.read")) throw redirect(303, "/");
  const { prefs } = await event.parent();
  const paging = resolvePaging(event.url, readTablePref(prefs, GOOGLE_ADS_DECISIONS_TABLE_ID));
  const includeWithdrawn = event.url.searchParams.get("withdrawn") === "1";
  const response = await apiFor(event).GET("/api/v1/google-ads/accounts/{account_id}/decisions", {
    params: {
      path: { account_id: event.params.accountId },
      query: {
        limit: paging.limit,
        offset: paging.offset,
        include_withdrawn: includeWithdrawn,
      },
    },
  });
  return {
    decisions: response.data?.items ?? [],
    total: response.data?.total ?? 0,
    includeWithdrawn,
    paging,
  };
};

export const actions: Actions = {
  /** Unsay a decision. The row survives, marked withdrawn and by whom. */
  withdraw: async (event) => {
    // The read is `account.read`; withdrawing is `policy.manage`, because it changes what will be
    // proposed next time. The control mirrors the key the call makes, not the key of the screen.
    if (!can(event.locals.user, "google_ads.policy.manage")) {
      return fail(403, { key: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const response = await apiFor(event).DELETE(
      "/api/v1/google-ads/accounts/{account_id}/decisions/{decision_id}",
      {
        params: {
          path: {
            account_id: event.params.accountId,
            decision_id: String(form.get("decision_id") ?? ""),
          },
        },
      },
    );
    if (response.error) return fail(400, apiErrorKey(response.error, "errors.server"));
    return { withdrawn: true };
  },
};
