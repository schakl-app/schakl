import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The account every tab under this section is about.
 *
 * It lives in the layout rather than in each page because there are four of them now — the
 * reports, the policy and the decisions log — and a section-shared lookup fetched once per page
 * is one of docs/PERFORMANCE.md's named mistakes.
 */
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "google_ads.account.read")) throw redirect(303, "/");
  const account = await apiFor(event).GET("/api/v1/google-ads/accounts/{account_id}", {
    params: { path: { account_id: event.params.accountId } },
  });
  if (!account.data) throw redirect(303, "/marketing/google-ads");
  return { account: account.data };
};
