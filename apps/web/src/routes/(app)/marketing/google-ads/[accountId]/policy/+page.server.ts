import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * A textarea of terms, one per line, as the API wants it.
 *
 * One per line rather than comma-separated because a Dutch search term contains commas far more
 * often than it contains newlines, and a separator that appears inside the values silently splits
 * one term into two nobody will ever match.
 */
function lines(raw: FormDataEntryValue | null): string[] {
  return String(raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * An optional number field. `""` is **inherit** and posts an explicit `null`; a value overrides.
 *
 * The distinction is the whole contract (CLAUDE.md §18): without an explicit null a ceiling set
 * once could never be taken off, and the account would stay pinned to whatever somebody typed in
 * a hurry. A checkbox cannot express three states, which is why this is a text field and not one.
 */
function optionalNumber(raw: FormDataEntryValue | null): number | null {
  const text = String(raw ?? "")
    .trim()
    .replace(",", ".");
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_ads.policy.manage")) {
    throw redirect(303, `/marketing/google-ads/${event.params.accountId}`);
  }
  const api = apiFor(event);
  // Both, in one load: the account's own row is what the form binds to, and the house row is what
  // the "follow the agency default" hints are labelled with — an inherit option that does not say
  // what it inherits is unreadable (#312, applied to a setting rather than to a comparison).
  const [own, house] = await Promise.all([
    api.GET("/api/v1/google-ads/accounts/{account_id}/policy", {
      params: { path: { account_id: event.params.accountId } },
    }),
    api.GET("/api/v1/google-ads/policy", {}),
  ]);
  if (!own.data) throw redirect(303, `/marketing/google-ads/${event.params.accountId}`);
  return { policy: own.data, house: house.data ?? null };
};

export const actions: Actions = {
  default: async (event) => {
    if (!can(event.locals.user, "google_ads.policy.manage")) {
      return fail(403, { key: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const response = await apiFor(event).PUT("/api/v1/google-ads/accounts/{account_id}/policy", {
      params: { path: { account_id: event.params.accountId } },
      body: {
        protected_terms: lines(form.get("protected_terms")),
        always_exclude: lines(form.get("always_exclude")),
        banned_phrases: lines(form.get("banned_phrases")),
        max_daily_budget: optionalNumber(form.get("max_daily_budget")),
        max_budget_increase_pct: optionalNumber(form.get("max_budget_increase_pct")),
        max_cpc: optionalNumber(form.get("max_cpc")),
        waste_min_cost: optionalNumber(form.get("waste_min_cost")),
        waste_min_clicks: optionalNumber(form.get("waste_min_clicks")),
        steering: String(form.get("steering") ?? ""),
        ad_copy_rules: String(form.get("ad_copy_rules") ?? ""),
      },
    });
    if (response.error) return fail(400, apiErrorKey(response.error, "errors.server"));
    return { saved: true };
  },
};
