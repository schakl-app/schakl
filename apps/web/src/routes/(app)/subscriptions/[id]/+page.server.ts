import { error, redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

/**
 * One agreement, read-only — the page a client opens from "Mijn abonnementen".
 *
 * The staff list edits in a modal and never needed a record page; a client has nothing to edit
 * and needs somewhere to *read* what they pay for: the lines, the interval, when it renews. The
 * API's portal repository scopes the read (a draft or another client's agreement is a 404, like
 * every other record), and it withholds the agency's working notes for an external login.
 * Staff may open it too — a link from an invoice or a report lands here as readily — and get the
 * same page plus the way to the list where it is edited.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "subscriptions.subscription.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const subscription_id = event.params.id;
  const [subscription, types] = await Promise.all([
    api.GET("/api/v1/subscriptions/{subscription_id}", {
      params: { path: { subscription_id }, query: { usage: true } },
    }),
    // The type vocabulary names the row; a client may read it (it is the label on their own
    // agreement, the way `contacts.type.read` is), and an inactive type still names a row.
    api.GET("/api/v1/subscriptions/types", { params: { query: { include_inactive: true } } }),
  ]);
  if (subscription.error || !subscription.data) {
    throw error(subscription.response?.status === 403 ? 403 : 404, "errors.not_found");
  }
  return {
    subscription: subscription.data,
    types: types.data ?? [],
    canWrite: can(event.locals.user, "subscriptions.subscription.write"),
    locale: event.locals.locale,
  };
};
