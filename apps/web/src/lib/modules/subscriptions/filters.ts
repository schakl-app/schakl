/**
 * What the agreements list can be narrowed by — the contract between the `+page.server.ts` load
 * (which reads these off the URL and maps them onto the API's parameter names) and the
 * `FilterBar` on the page (which renders them).
 *
 * The keys are the short ones, because they are the address bar: a client card links to
 * `/subscriptions?company=<id>` and someone pastes that link into a chat.
 *
 * `status` and `type` are the two that matter here (#354). They used to be nine identical
 * plain-text chips in one row — four statuses then five tenant-defined types, same weight, same
 * colour, no divider and no label — so nothing said that pressing *Opgezegd* and pressing
 * *Hosting* narrow along different axes. They are two controls now, and the shared bar is what
 * makes them look like the same two controls the domain register and the website list have.
 */
export const SUBSCRIPTION_FILTERS = ["q", "company", "status", "type"] as const;

export type SubscriptionFilterKey = (typeof SUBSCRIPTION_FILTERS)[number];
