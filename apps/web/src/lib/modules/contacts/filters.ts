/**
 * What the address book can be narrowed by — the keys the URL carries and the `FilterBar`
 * renders. Short, because they are the address bar and people paste those links.
 *
 * `type` is the tenant's own vocabulary (contact types), so the pill row is empty on an
 * instance that has defined none and the control is simply not drawn.
 */
export const CONTACT_FILTERS = ["q", "company", "type"] as const;

export type ContactFilterKey = (typeof CONTACT_FILTERS)[number];
