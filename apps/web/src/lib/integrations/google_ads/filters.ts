/**
 * What a Google Ads report can be narrowed by, and which of them each view offers.
 *
 * Business-licensed — see LICENSE.
 *
 * The contract between the `+page.server.ts` load (which reads these off the URL and maps them
 * onto the API's own parameter names) and the `FilterBar` on the page (which renders them). Both
 * read `page.url`, so what the controls show and what Google was asked for cannot disagree.
 *
 * **The set is per view, because the rows are.** A status filter over a negative-keyword list or
 * a change history would answer nothing at all, silently — those rows carry no status — and a
 * minimum cost over a list with no metrics on it is the same mistake. The API refuses to offer
 * what it cannot apply (`_status_param` exists for exactly this reason); this is that same
 * judgement, made once, on the screen that draws the control.
 */
import { t } from "$lib/core/i18n";
import type { FilterDef } from "$lib/core/filters/types";

import type { ReportView } from "./columns";

/** Every key any view owns. What "wissen" clears, and what the load reads. */
export const GOOGLE_ADS_FILTERS = ["q", "status", "mincost"] as const;

export type GoogleAdsFilterKey = (typeof GOOGLE_ADS_FILTERS)[number];

/** The views whose rows carry a Google status worth filtering on. */
const HAS_STATUS: ReportView[] = ["campaigns", "keywords"];

/** The views whose rows carry money, so "at least this much" is a question about them. */
const HAS_COST: ReportView[] = ["search-terms"];

/**
 * A minimum spend, as a short ladder rather than a number box.
 *
 * The job this serves is reviewing search terms, and the question there is always "show me the
 * ones that cost real money" — not a figure anyone has in mind. The labels carry the account's
 * **own** currency, never the tenant's: an agency in Amsterdam runs accounts billed in GBP.
 */
const COST_STEPS = [1, 5, 10, 25, 50];

export function reportFilters(view: ReportView, currency: string | null): FilterDef[] {
  const money = (amount: number) =>
    new Intl.NumberFormat(undefined, {
      ...(currency ? { style: "currency", currency } : { style: "decimal" }),
      maximumFractionDigits: 0,
    }).format(amount);

  return [
    { kind: "search", key: "q", placeholder: t(`google_ads.filter.search.${view}`) },
    {
      kind: "pills",
      key: "status",
      hidden: !HAS_STATUS.includes(view),
      options: [
        { value: "ENABLED", label: t("google_ads.enum.enabled") },
        { value: "PAUSED", label: t("google_ads.enum.paused") },
        { value: "REMOVED", label: t("google_ads.enum.removed") },
      ],
    },
    {
      kind: "select",
      key: "mincost",
      hidden: !HAS_COST.includes(view),
      placeholder: t("google_ads.filter.min_cost"),
      // Self-describing, because a Combobox shows the selected label and nothing else: "vanaf
      // € 10" survives being read on its own, "10" under a placeholder nobody can see does not.
      options: COST_STEPS.map((amount) => ({
        value: String(amount),
        label: t("google_ads.filter.min_cost_from", { amount: money(amount) }),
      })),
    },
  ];
}
