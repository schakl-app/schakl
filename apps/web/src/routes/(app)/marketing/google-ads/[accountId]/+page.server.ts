import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/** The report this page shows. The URL is the view (docs/PERFORMANCE.md, CLAUDE.md §9). */
const VIEWS = ["trend", "campaigns", "keywords", "search-terms", "negatives", "changes"] as const;
type View = (typeof VIEWS)[number];

function isView(value: string): value is View {
  return (VIEWS as readonly string[]).includes(value);
}

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google_ads.account.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const accountId = event.params.accountId;

  // An unknown tab falls back rather than 404ing: a tab name arrives from a URL anyone can edit
  // and an old bookmark can carry, and a broken link should land on the report, not an error.
  const raw = event.url.searchParams.get("view") ?? "campaigns";
  const view: View = isView(raw) ? raw : "campaigns";
  const period = event.url.searchParams.get("period") ?? "30d";

  // One live Google call per view, streamed behind the shell: the heading, the tabs and the
  // period picker are what the user interacts with, and none of them needs Google to have
  // answered (docs/PERFORMANCE.md). Negatives carry no period — an exclusion is configuration.
  //
  // `trend` is the exception and the point of the nightly mirror: it reads schakl's own stored
  // rows, so it is fast, spends no Ads quota, and still renders when Google is down.
  const query = { period };
  const report =
    view === "trend"
      ? api.GET("/api/v1/google-ads/accounts/{account_id}/trend", {
          params: { path: { account_id: accountId }, query },
        })
      : view === "negatives"
        ? api.GET("/api/v1/google-ads/accounts/{account_id}/negatives", {
            params: { path: { account_id: accountId } },
          })
        : view === "keywords"
          ? api.GET("/api/v1/google-ads/accounts/{account_id}/keywords", {
              params: { path: { account_id: accountId }, query },
            })
          : view === "search-terms"
            ? api.GET("/api/v1/google-ads/accounts/{account_id}/search-terms", {
                params: { path: { account_id: accountId }, query },
              })
            : view === "changes"
              ? api.GET("/api/v1/google-ads/accounts/{account_id}/changes", {
                  params: { path: { account_id: accountId }, query },
                })
              : api.GET("/api/v1/google-ads/accounts/{account_id}/campaigns", {
                  params: { path: { account_id: accountId }, query },
                });

  return {
    view,
    period,
    // The API's own error envelope reaches the page as an **i18n key** rather than throwing: a
    // refused Google call is a state this screen draws ("reconnect", "the developer token is
    // not approved", "this API version is sunset"), not a 500. Narrowed here with
    // `apiErrorKey`, because the envelope is not in the OpenAPI spec and the generated error
    // type describes only FastAPI's default validation shape.
    report: report.then((r) => ({
      data: r.data ?? null,
      errorKey: r.error ? apiErrorKey(r.error, "errors.server").key : null,
    })),
  };
};

export const actions: Actions = {
  /**
   * One pass over a search-terms list: exclude some, keep the rest, in one request.
   *
   * Both halves together because that is what a review *is*. Excluding eight of a hundred terms
   * is also a decision about the other ninety-two, and a log holding only the exclusions makes
   * the same ninety-two candidates again next month — until the account manager stops reading
   * the list. The API takes them in one call for the same reason.
   */
  review: async (event) => {
    // Mirrors the key the call actually makes (#310): `POST /negatives` declares
    // `negative.write`, which is not what the screen is *about* (`account.read`).
    if (!can(event.locals.user, "google_ads.negative.write")) {
      return fail(403, { key: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const excludeTerms = form.getAll("exclude").map(String).filter(Boolean);
    const keepTerms = form.getAll("keep").map(String).filter(Boolean);
    const campaignId = String(form.get("campaign_id") ?? "");
    const reason = String(form.get("reason") ?? "");
    if (!campaignId || (excludeTerms.length === 0 && keepTerms.length === 0)) {
      return fail(400, { key: "google_ads.review.nothing_selected" });
    }
    const response = await apiFor(event).POST(
      "/api/v1/google-ads/accounts/{account_id}/negatives",
      {
        params: { path: { account_id: event.params.accountId } },
        body: {
          // Not a dry run: the button says "save", and a control that silently validated would
          // be the worst kind of no-op — it reports success and changes nothing.
          validate_only: false,
          level: "campaign",
          parent_id: campaignId,
          // The exclusion written for a search term is PHRASE rather than EXACT on purpose: the
          // term Google reported is one spelling of a query, and an exact negative leaves every
          // near-variant of it still spending. The protected-terms guard is what makes the wider
          // match safe to default to.
          terms: excludeTerms.map((text) => ({ text, match_type: "PHRASE", reason })),
          keep: keepTerms.map((text) => ({ text, reason })),
        },
      },
    );
    if (response.error) return fail(400, apiErrorKey(response.error, "errors.server"));
    return { outcome: response.data };
  },

  /** Pause or resume one campaign. Gated on the campaign key, which is not the budget key. */
  campaign_status: async (event) => {
    if (!can(event.locals.user, "google_ads.campaign.write")) {
      return fail(403, { key: "errors.forbidden" });
    }
    const form = await event.request.formData();
    const response = await apiFor(event).PATCH(
      "/api/v1/google-ads/accounts/{account_id}/campaigns/{campaign_id}",
      {
        params: {
          path: {
            account_id: event.params.accountId,
            campaign_id: String(form.get("campaign_id") ?? ""),
          },
        },
        body: { validate_only: false, status: String(form.get("status") ?? "PAUSED") },
      },
    );
    if (response.error) return fail(400, apiErrorKey(response.error, "errors.server"));
    return { outcome: response.data };
  },
};
