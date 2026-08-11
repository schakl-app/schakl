import { redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { PageServerLoad } from "./$types";

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

  const account = await api.GET("/api/v1/google-ads/accounts/{account_id}", {
    params: { path: { account_id: accountId } },
  });
  if (!account.data) throw redirect(303, "/marketing/google-ads");

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
    account: account.data,
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
