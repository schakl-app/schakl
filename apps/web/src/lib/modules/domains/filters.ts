/**
 * What the domain register can be narrowed by — the contract between the `+page.server.ts`
 * load (which reads these off the URL and maps them onto the API's parameter names) and the
 * `FilterBar` on the page (which renders them with labels and the section's own lookups).
 *
 * The keys are the short ones, because they are the address bar: a client card links to
 * `/domains?company=<id>`, and someone pastes that link into a chat.
 *
 * Six, and each is a question an agency actually asks of its register: whose is it, is it live,
 * where is it registered, who answers its DNS, do we bill for it, and what was that name again.
 * Every one is a query parameter the API applies — a filter applied in the browser narrows the
 * page that happened to load and reports a total counted over everything (docs/PERFORMANCE.md).
 */
export const DOMAIN_FILTERS = [
  "q",
  "company",
  "status",
  "registrar",
  "dns",
  "invoiceable",
] as const;

export type DomainFilterKey = (typeof DOMAIN_FILTERS)[number];
