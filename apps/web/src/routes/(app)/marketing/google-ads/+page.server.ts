import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { marketingConnectActions } from "$lib/modules/marketing/actions.server";

import type { Actions, PageServerLoad } from "./$types";

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
    // The connect dialog's client picker writes a *marketing link*, so it is that permission
    // that decides whether the ＋ is drawn. The API is the boundary either way (docs/UX.md).
    canLink: can(event.locals.user, "marketing.link.manage"),
    locale: event.locals.locale,
  };
};

/**
 * Connecting an account from here (#338) instead of sending everyone to Instellingen to type a
 * customer id. It posts to `POST /marketing/links`, the write path that records both this
 * module's account row and the marketing link — so a client connected here is connected
 * everywhere, which is the whole point of the issue.
 */
export const actions: Actions = {
  ...marketingConnectActions,
  createCompany: createCompanyAction,
};
