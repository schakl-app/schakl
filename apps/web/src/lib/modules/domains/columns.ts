/**
 * The columns the domain list can show (#251, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte: the page's server `load` reads this to resolve the saved layout
 * and the sort, and the component reads the same list to render headers and cells.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/domains/service.py::SORTABLE`),
 * which covers every column: company by the client's name, registrar/DNS by the provider's
 * name — all server-side, because the list is paginated.
 *
 * Every non-primary column declares a `width`, because the table lays out `table-fixed` and an
 * undeclared one falls back to a share of whatever is left rather than to its content. The
 * three shown by default add up to ~600px, so the register fits a laptop beside the primary
 * column that absorbs the slack; the opt-in ones are sized for what they actually hold (a date,
 * a price, a yes/no) so switching one on does not squeeze the name.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const DOMAINS_TABLE_ID = "domains";

export const DOMAIN_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "domains.name", sortKey: "name", primary: true, width: 240 },
  {
    key: "company",
    labelKey: "domains.company",
    sortKey: "company",
    defaultVisible: true,
    width: 260,
  },
  {
    key: "status",
    labelKey: "domains.status",
    sortKey: "status",
    defaultVisible: true,
    width: 140,
  },
  {
    key: "registrar",
    labelKey: "domains.registrar",
    sortKey: "registrar",
    defaultVisible: true,
    width: 200,
    // The register we renew at is our supplier, not the client's business.
    audience: "staff",
  },
  { key: "dns", labelKey: "domains.dns", sortKey: "dns", width: 200 },
  { key: "dnssec", labelKey: "domains.dns.dnssec", sortKey: "dnssec", width: 110 },
  {
    key: "email_enabled",
    labelKey: "domains.email_enabled",
    sortKey: "email_enabled",
    width: 130,
  },
  // Renewal + resolved price (#250). The price is resolved per row (override → TLD list),
  // which the server cannot order by — an honest quiet header (docs/UX.md).
  {
    key: "next_invoice",
    labelKey: "domains.renewal",
    sortKey: "next_invoice_date",
    align: "right",
    width: 130,
  },
  // What the registrar last observed, beside the date we bill on. Its own column rather than a
  // badge on the one above, because the reason to switch it on is to *sort a list by drift* —
  // "which of these does the registrar disagree with me about" is the question, and it is off by
  // default because an instance with no register connected would only ever see dashes. Quiet
  // header: the value is a correlated subquery over another module's table, not a sortable one.
  {
    key: "register_expires",
    labelKey: "domains.register_expiry.column",
    align: "right",
    width: 140,
  },
  { key: "price", labelKey: "domains.price", align: "right", width: 110 },
  // Resolved server-side from a three-state flag and the registrar registers (#298), so there
  // is nothing to sort by — a quiet header, like the price beside it. Wider than a plain yes/no
  // because the "volgt register" badge sits beside the answer.
  // Whether *we* bill the renewal is the agency's decision, not a fact about the domain.
  { key: "invoiceable", labelKey: "domains.invoiceable.column", width: 170, audience: "staff" },
  {
    key: "created_at",
    labelKey: "table.column.created_at",
    sortKey: "created_at",
    align: "right",
    width: 130,
  },
];

export const TLD_PRICES_TABLE_ID = "domain-tld-prices";

/** The TLD price list (#250): one row per TLD, grouped client-side from the full (small)
 * price history, so every column is a quiet header — there is no server sort to mirror. */
export const TLD_PRICE_COLUMNS: ColumnMeta[] = [
  { key: "tld", labelKey: "domains.tld_prices.tld", primary: true, width: 140 },
  {
    key: "current",
    labelKey: "domains.tld_prices.current",
    defaultVisible: true,
    align: "right",
    width: 140,
  },
  {
    key: "since",
    labelKey: "domains.tld_prices.since",
    defaultVisible: true,
    align: "right",
    width: 140,
  },
  // The widest of the four: an upcoming price is an amount *and* the date it starts.
  { key: "upcoming", labelKey: "domains.tld_prices.upcoming", defaultVisible: true, width: 220 },
  {
    key: "domains",
    labelKey: "domains.tld_prices.domains",
    defaultVisible: true,
    align: "right",
    width: 120,
  },
];
