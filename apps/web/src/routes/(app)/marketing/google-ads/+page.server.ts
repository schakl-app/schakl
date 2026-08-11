import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  // The API enforces it too; redirect rather than showing a bare page — the nav item is already
  // hidden for anyone without the permission (docs/UX.md).
  if (!can(event.locals.user, "google_ads.account.read")) throw redirect(303, "/");
  const api = apiFor(event);

  // Accounts and clients in one fan: the list is small (one row per linked advertiser) and the
  // client names are what turn a customer id into something a human recognises. `count: false`
  // because nothing on this page shows a total (docs/PERFORMANCE.md).
  const [accounts, companies] = await Promise.all([
    api.GET("/api/v1/google-ads/accounts", { params: { query: { active_only: true } } }),
    api.GET("/api/v1/companies", {
      params: { query: { limit: 200, offset: 0, count: false, sort: "name" } },
    }),
  ]);

  return {
    accounts: accounts.data ?? [],
    companies: companies.data?.items ?? [],
    canManage: can(event.locals.user, "google_ads.settings.manage"),
  };
};
