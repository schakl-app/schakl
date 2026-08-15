import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { readFilters } from "$lib/core/filters/types";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { readTablePref } from "$lib/core/table/columns";
import { resolvePaging } from "$lib/core/table/paging";
import { parseTablePref, saveTablePref } from "$lib/core/table/prefs.server";
import { reportTableId, type ReportView } from "$lib/integrations/google_ads/columns";
import { GOOGLE_ADS_FILTERS } from "$lib/integrations/google_ads/filters";

import type { Actions, PageServerLoad } from "./$types";

/** The report this page shows. The URL is the view (docs/PERFORMANCE.md, CLAUDE.md §9). */
const VIEWS = ["trend", "campaigns", "keywords", "search-terms", "negatives", "changes"] as const;
type View = (typeof VIEWS)[number];

function isView(value: string): value is View {
  return (VIEWS as readonly string[]).includes(value);
}

/** The tab whose page size is being remembered. `trend` is a summary and has no table. */
function tableFor(view: View): ReportView {
  return view === "trend" ? "campaigns" : view;
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

  // Filters and page from the URL, resolved here and applied by the API. The short keys are the
  // address bar; mapping them onto Google's own parameter names is this load's job. The saved
  // size is only the default — `?size=` wins whenever it speaks (`core/table/paging.ts`).
  const filters = readFilters(event.url, [...GOOGLE_ADS_FILTERS]);
  const { prefs } = await event.parent();
  const paging = resolvePaging(event.url, readTablePref(prefs, reportTableId(tableFor(view))));

  // One live Google call per view, streamed behind the shell: the heading, the tabs, the filter
  // bar and the period picker are what the user interacts with, and none of them needs Google to
  // have answered (docs/PERFORMANCE.md). Negatives carry no period — an exclusion is
  // configuration. Paging costs no extra call: the read fetches its own ceiling either way and
  // hands back the slice, which is why `total_rows` is a real number and not an estimate.
  //
  // `trend` is the exception and the point of the nightly mirror: it reads schakl's own stored
  // rows, so it is fast, spends no Ads quota, and still renders when Google is down.
  const query = { period };
  const paged = {
    ...query,
    q: filters.q,
    limit: paging.limit,
    offset: paging.offset,
  };
  const report =
    view === "trend"
      ? api.GET("/api/v1/google-ads/accounts/{account_id}/trend", {
          params: { path: { account_id: accountId }, query },
        })
      : view === "negatives"
        ? api.GET("/api/v1/google-ads/accounts/{account_id}/negatives", {
            params: {
              path: { account_id: accountId },
              query: { q: filters.q, limit: paging.limit, offset: paging.offset },
            },
          })
        : view === "keywords"
          ? api.GET("/api/v1/google-ads/accounts/{account_id}/keywords", {
              params: {
                path: { account_id: accountId },
                query: { ...paged, status: filters.status },
              },
            })
          : view === "search-terms"
            ? api.GET("/api/v1/google-ads/accounts/{account_id}/search-terms", {
                params: {
                  path: { account_id: accountId },
                  // A minimum spend the API applies in the GAQL, so it narrows the list the page
                  // is taken from rather than the page. Junk in the URL degrades to no filter.
                  query: { ...paged, min_cost: Number(filters.mincost) || undefined },
                },
              })
            : view === "changes"
              ? api.GET("/api/v1/google-ads/accounts/{account_id}/changes", {
                  params: { path: { account_id: accountId }, query: paged },
                })
              : api.GET("/api/v1/google-ads/accounts/{account_id}/campaigns", {
                  params: {
                    path: { account_id: accountId },
                    query: { ...paged, status: filters.status },
                  },
                });

  return {
    view,
    period,
    filters,
    paging,
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
   * Remember this view's page size for next time. The navigation is `Pagination`'s own.
   *
   * Per view, because the tabs are different tables (`reportTableId`). Written through the shared
   * `parseTablePref`/`saveTablePref` pair rather than a bespoke `{page_size}` write: `/prefs`
   * replaces a list's entry wholesale, so the day this screen grows a column picker, a
   * hand-rolled size write here would silently erase the layout it never knew about.
   */
  saveTable: async (event) => {
    const form = await event.request.formData();
    const raw = String(form.get("view") ?? "");
    const view = isView(raw) ? raw : "campaigns";
    await saveTablePref(event, reportTableId(tableFor(view)), parseTablePref(form));
    return { saved: true };
  },

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
