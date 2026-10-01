import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { parseAmount } from "$lib/integrations/meta_ads/format";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The guardrails: this account's own, and the house's beside them.
 *
 * Every control on this screen writes, so the screen is gated whole on the key its save
 * makes (docs/UX.md). Both layers are on one page because an account's policy is a *diff*
 * over the house's, and a diff read without what it differs from is a list of numbers.
 *
 * A form that replaces a record is never rendered over a guess: if either read fails the
 * page refuses to draw, rather than offering a blank form whose save would erase the policy.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "meta_ads.policy.manage")) {
    throw redirect(303, `/marketing/meta-ads/${event.params.accountId}`);
  }
  const api = apiFor(event);
  const [own, house] = await Promise.all([
    api.GET("/api/v1/meta-ads/accounts/{account_id}/policy", {
      params: { path: { account_id: event.params.accountId } },
    }),
    api.GET("/api/v1/meta-ads/policy"),
  ]);
  if (!own.data || !house.data) throw error(502, "errors.server");
  return { policy: own.data, house: house.data };
};

/** A textarea of phrases, one per line — a comma is a character an ad may contain. */
function lines(raw: FormDataEntryValue | null): string[] {
  return String(raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** A typed amount as cents. Blank is `null`: inherit. Anything else unparseable is refused. */
function amount(raw: FormDataEntryValue | null): number | null | undefined {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  return parseAmount(text) ?? undefined;
}

function percent(raw: FormDataEntryValue | null): number | null | undefined {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= 0 ? value : undefined;
}

function body(form: FormData) {
  const daily = amount(form.get("max_daily_budget"));
  const lifetime = amount(form.get("max_lifetime_budget"));
  const increase = percent(form.get("max_budget_increase_pct"));
  const fields: Record<string, string> = {};
  if (daily === undefined) fields.max_daily_budget = "errors.meta_ads_budget_invalid";
  if (lifetime === undefined) fields.max_lifetime_budget = "errors.meta_ads_budget_invalid";
  if (increase === undefined) fields.max_budget_increase_pct = "errors.validation";
  if (Object.keys(fields).length > 0) return { fields };
  return {
    values: {
      max_daily_budget_cents: daily ?? null,
      max_lifetime_budget_cents: lifetime ?? null,
      max_budget_increase_pct: increase ?? null,
      banned_phrases: lines(form.get("banned_phrases")),
      dsa_beneficiary: String(form.get("dsa_beneficiary") ?? "").trim() || null,
      dsa_payor: String(form.get("dsa_payor") ?? "").trim() || null,
      steering: String(form.get("steering") ?? ""),
    },
  };
}

export const actions: Actions = {
  save: async (event) => {
    const parsed = body(await event.request.formData());
    if (parsed.fields) {
      return fail(400, { error: "errors.validation", fields: parsed.fields, layer: "own" });
    }
    const { error: failure } = await apiFor(event).PUT(
      "/api/v1/meta-ads/accounts/{account_id}/policy",
      { params: { path: { account_id: event.params.accountId } }, body: parsed.values },
    );
    if (failure) return fail(400, { ...apiErrorKey(failure, "errors.server"), layer: "own" });
    return { saved: "own" };
  },

  saveHouse: async (event) => {
    const parsed = body(await event.request.formData());
    if (parsed.fields) {
      return fail(400, { error: "errors.validation", fields: parsed.fields, layer: "house" });
    }
    const { error: failure } = await apiFor(event).PUT("/api/v1/meta-ads/policy", {
      body: parsed.values,
    });
    if (failure) return fail(400, { ...apiErrorKey(failure, "errors.server"), layer: "house" });
    return { saved: "house" };
  },

  clear: async (event) => {
    const { error: failure } = await apiFor(event).DELETE(
      "/api/v1/meta-ads/accounts/{account_id}/policy",
      { params: { path: { account_id: event.params.accountId } } },
    );
    if (failure) return fail(400, { ...apiErrorKey(failure, "errors.server"), layer: "own" });
    return { saved: "cleared" };
  },
};
