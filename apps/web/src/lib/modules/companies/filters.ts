/**
 * What the client register can be narrowed by — the keys the URL carries and the `FilterBar`
 * renders. Short, because they are the address bar and people paste those links.
 *
 * `status` is the one with a rule on it (#329): its *absent* state is the working book of
 * business — every status but archived — so the default is a pill of its own and "Alles" carries
 * its own token, or the archive would be a view you can reach and cannot link to.
 */
export const COMPANY_FILTERS = ["q", "mine", "status"] as const;

export type CompanyFilterKey = (typeof COMPANY_FILTERS)[number];
