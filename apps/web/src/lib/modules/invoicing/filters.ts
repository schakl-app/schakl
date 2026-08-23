/**
 * What the invoice and quote registers can be narrowed by — the keys the URL carries and the
 * `FilterBar` renders. One list for both, because they are the same three questions and two
 * copies would drift the first time either grew a fourth.
 *
 * The client picker is hidden rather than absent for a portal login: a client is looking at
 * their own documents, so a control for choosing *whose* has nothing to offer them (#266).
 */
export const DOCUMENT_FILTERS = ["q", "company", "status"] as const;

export type DocumentFilterKey = (typeof DOCUMENT_FILTERS)[number];

/**
 * The invoice register's own extra: "only the overdue ones", which the summary strip's red tile
 * also sets. Two controls for one filter is deliberate — the tile is where a reader *notices* the
 * number, the chip is what says the list is narrowed and what "wissen" clears.
 */
export const INVOICE_FILTERS = [...DOCUMENT_FILTERS, "overdue"] as const;

export type InvoiceFilterKey = (typeof INVOICE_FILTERS)[number];
