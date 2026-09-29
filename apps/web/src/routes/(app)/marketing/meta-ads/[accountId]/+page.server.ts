import { fail } from "@sveltejs/kit";

import type { components } from "$lib/core/api/schema";
import { apiErrorKey, streamed } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { PERIODS } from "$lib/integrations/meta_ads/format";

import type { Actions, PageServerLoad } from "./$types";

type Campaign = components["schemas"]["MetaAdsCampaignRead"];
type AdSet = components["schemas"]["MetaAdsAdSetRead"];
type Ad = components["schemas"]["MetaAdsAdRead"];
type Insights = components["schemas"]["MetaAdsInsightsRead"];

const KINDS = ["campaigns", "adsets", "ads"] as const;
type Kind = (typeof KINDS)[number];

/**
 * One ad account's campaigns, with what each cost and returned.
 *
 * **The shell is stored and the numbers stream.** The account is the layout's (a row), so the
 * heading, the tabs and every control draw at once; the reads that ask Meta are returned
 * unawaited and settle behind it (docs/PERFORMANCE.md).
 *
 * **The requests are counted, because they are the budget.** An app without Meta's review is
 * on the Marketing API's limited tier: sixty points per ad account per five minutes, a read
 * costing one. This page spends three (the campaigns, their insights, the account's totals)
 * and two more when a campaign is opened — so it can be opened a dozen times in five minutes,
 * and it asks for nothing it does not draw.
 */
export const load: PageServerLoad = async (event) => {
  event.depends("meta_ads:account");
  const api = apiFor(event);
  const account_id = event.params.accountId;
  const raw = event.url.searchParams.get("period") ?? "30d";
  const period = (PERIODS as readonly string[]).includes(raw) ? raw : "30d";
  const campaign = event.url.searchParams.get("campaign") ?? "";
  const path = { account_id };

  return {
    period,
    campaign,
    campaigns: streamed<Campaign[]>(
      api.GET("/api/v1/meta-ads/accounts/{account_id}/campaigns", { params: { path } }),
    ),
    insights: streamed<Insights>(
      api.GET("/api/v1/meta-ads/accounts/{account_id}/insights", {
        params: { path, query: { level: "campaign", period } },
      }),
    ),
    // Only when a campaign is open: two reads nobody asked for are two points nobody has.
    adsets: campaign
      ? streamed<AdSet[]>(
          api.GET("/api/v1/meta-ads/accounts/{account_id}/adsets", {
            params: { path, query: { campaign_meta_id: campaign } },
          }),
        )
      : null,
    ads: campaign
      ? streamed<Ad[]>(
          api.GET("/api/v1/meta-ads/accounts/{account_id}/ads", {
            params: { path, query: { campaign_meta_id: campaign } },
          }),
        )
      : null,
  };
};

function kindOf(value: FormDataEntryValue | null): Kind | null {
  const text = String(value ?? "");
  return (KINDS as readonly string[]).includes(text) ? (text as Kind) : null;
}

export const actions: Actions = {
  /**
   * Switch on, or pause. The two are different permissions — switching on is the act that
   * spends — and the gate mirrors the key the call makes, never a broader one (#310).
   */
  status: async (event) => {
    const form = await event.request.formData();
    const kind = kindOf(form.get("kind"));
    const meta_id = String(form.get("meta_id") ?? "");
    const activate = form.get("to") === "ACTIVE";
    if (!kind || !meta_id) return fail(400, { error: "errors.validation" });
    const key = activate ? "meta_ads.ads.activate" : "meta_ads.campaign.write";
    if (!can(event.locals.user, key)) return fail(403, { error: "errors.forbidden" });
    const api = apiFor(event);
    const params = { path: { account_id: event.params.accountId, kind, meta_id } };
    const body = { reason: String(form.get("reason") ?? "").trim(), validate_only: false };
    const { data, error } = activate
      ? await api.POST("/api/v1/meta-ads/accounts/{account_id}/{kind}/{meta_id}/activate", {
          params,
          body,
        })
      : await api.POST("/api/v1/meta-ads/accounts/{account_id}/{kind}/{meta_id}/pause", {
          params,
          body,
        });
    if (error) return refusal(error);
    return { done: activate ? "activated" : "paused", meta_id: data?.meta_id ?? meta_id };
  },

  budget: async (event) => {
    if (!can(event.locals.user, "meta_ads.budget.write")) {
      return fail(403, { error: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const kind = kindOf(form.get("kind"));
    const meta_id = String(form.get("meta_id") ?? "");
    const cents = Number(form.get("cents") ?? "");
    const which = form.get("which") === "lifetime" ? "lifetime" : "daily";
    if (!kind || kind === "ads" || !meta_id) return fail(400, { error: "errors.validation" });
    if (!Number.isInteger(cents) || cents <= 0) {
      return fail(400, { error: "errors.meta_ads_budget_invalid", meta_id });
    }
    const api = apiFor(event);
    const body = {
      ...(which === "daily" ? { daily_budget_cents: cents } : { lifetime_budget_cents: cents }),
      reason: String(form.get("reason") ?? "").trim(),
      validate_only: false,
    };
    const { error } =
      kind === "campaigns"
        ? await api.PUT(
            "/api/v1/meta-ads/accounts/{account_id}/campaigns/{campaign_meta_id}/budget",
            {
              params: {
                path: { account_id: event.params.accountId, campaign_meta_id: meta_id },
              },
              body,
            },
          )
        : await api.PUT("/api/v1/meta-ads/accounts/{account_id}/adsets/{adset_meta_id}/budget", {
            params: { path: { account_id: event.params.accountId, adset_meta_id: meta_id } },
            body,
          });
    if (error) return refusal(error, meta_id);
    return { done: "budget", meta_id };
  },
};

/**
 * A refusal, with its numbers. The envelope's `details` carry the limit and what was asked
 * (CLAUDE.md §9), so the sentence can name the ceiling rather than allude to one.
 */
function refusal(error: unknown, meta_id?: string) {
  const { key } = apiErrorKey(error, "errors.server");
  const details = (error as { error?: { details?: Record<string, unknown> } })?.error?.details;
  return fail(400, { error: key, details: details ?? null, meta_id: meta_id ?? null });
}
