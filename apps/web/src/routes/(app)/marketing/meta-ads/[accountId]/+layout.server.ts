import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { LayoutServerLoad } from "./$types";

/**
 * The account every tab under this section is about — the **stored** row, so the section's
 * chrome draws at once and only the tab's own read waits for Meta.
 */
export const load: LayoutServerLoad = async (event) => {
  if (!can(event.locals.user, "meta_ads.account.read")) throw redirect(303, "/");
  const account = await apiFor(event).GET("/api/v1/meta-ads/accounts/{account_id}", {
    params: { path: { account_id: event.params.accountId } },
  });
  if (!account.data) throw redirect(303, "/marketing/meta-ads");
  return {
    account: account.data,
    canWrite: can(event.locals.user, "meta_ads.campaign.write") && account.data.can_write,
    canBudget: can(event.locals.user, "meta_ads.budget.write") && account.data.can_write,
    canActivate: can(event.locals.user, "meta_ads.ads.activate") && account.data.can_write,
    canPolicy: can(event.locals.user, "meta_ads.policy.manage"),
  };
};
