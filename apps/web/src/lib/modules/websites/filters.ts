/**
 * What the websites list can be narrowed by — the same contract `domains/filters.ts` describes,
 * shared between this section's load and its `FilterBar`.
 *
 * Four, because a website is a thin record: it is somebody's, it sits on a server, it is either
 * watched or it isn't, and its name is its parent domain's. `q` and `company` therefore both
 * ask about the *domain* — the API crosses that bridge itself, so the screen never has to.
 */
export const WEBSITE_FILTERS = ["q", "company", "hosting", "uptime"] as const;

export type WebsiteFilterKey = (typeof WEBSITE_FILTERS)[number];
