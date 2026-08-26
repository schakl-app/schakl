/**
 * What the project list can be narrowed by — the keys the URL carries and the `FilterBar`
 * renders. Short, because they are the address bar and people paste those links.
 *
 * `status` follows the register rule (#329): absent is the working set — every status but
 * archived — and `all` is its own token, so "everything, archive included" is a view somebody
 * can link to. `unnamed` gathers the abandoned create-then-edit rows (#350) and is orthogonal
 * to it: a nameless project has a status like any other.
 */
export const PROJECT_FILTERS = ["q", "company", "mine", "status", "unnamed", "burn"] as const;

export type ProjectFilterKey = (typeof PROJECT_FILTERS)[number];
