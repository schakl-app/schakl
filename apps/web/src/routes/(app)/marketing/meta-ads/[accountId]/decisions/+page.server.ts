import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";

import type { Actions, PageServerLoad } from "./$types";

const TABLE_ID = "meta_ads_decisions";

/**
 * What was changed in this account from here, by whom and why — newest first, paged.
 * Stored rows: nothing on this tab asks Meta anything.
 */
export const load: PageServerLoad = async (event) => {
  const { prefs } = await event.parent();
  const paging = resolvePaging(event.url, readTablePref(prefs, TABLE_ID));
  const decisions = await apiFor(event).GET("/api/v1/meta-ads/accounts/{account_id}/decisions", {
    params: {
      path: { account_id: event.params.accountId },
      query: { limit: paging.limit, offset: paging.offset },
    },
  });
  return {
    decisions: decisions.data?.items ?? [],
    total: decisions.data?.total ?? 0,
    loadError: decisions.error ? apiErrorKey(decisions.error, "errors.server").key : null,
    paging,
  };
};

export const actions: Actions = {
  saveTable: async (event) => {
    const form = await event.request.formData();
    await saveTablePref(event, TABLE_ID, parseTablePref(form));
    return { tableSaved: true };
  },
};
