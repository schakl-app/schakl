import { redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

/**
 * The linked Meta ad accounts. Stored rows, one call — nothing here asks Meta anything, so
 * the list opens at the speed of the database whatever the Marketing API's mood.
 *
 * Linking an account to a client is done with the channels (Marketing → Social → Kanalen):
 * an ad account is found by the same token, on the same screen, in the same gesture.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meta_ads.account.read")) throw redirect(303, "/");
  const accounts = await apiFor(event).GET("/api/v1/meta-ads/accounts");
  return {
    accounts: accounts.data ?? [],
    loadError: accounts.error ? apiErrorKey(accounts.error, "errors.server").key : null,
    canManage: can(event.locals.user, "meta.settings.manage"),
  };
};
