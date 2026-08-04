/**
 * The columns the domain list can show (#251, the shared DataTable — #24).
 *
 * Plain metadata, no Svelte: the page's server `load` reads this to resolve the saved layout
 * and the sort, and the component reads the same list to render headers and cells.
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/domains/service.py::SORTABLE`),
 * which covers every column: company by the client's name, registrar/DNS by the provider's
 * name — all server-side, because the list is paginated.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const DOMAINS_TABLE_ID = "domains";

export const DOMAIN_COLUMNS: ColumnMeta[] = [
  { key: "name", labelKey: "domains.name", sortKey: "name", primary: true, width: 240 },
  { key: "company", labelKey: "domains.company", sortKey: "company", defaultVisible: true },
  { key: "status", labelKey: "domains.status", sortKey: "status", defaultVisible: true },
  { key: "registrar", labelKey: "domains.registrar", sortKey: "registrar", defaultVisible: true },
  { key: "dns", labelKey: "domains.dns", sortKey: "dns" },
  { key: "dnssec", labelKey: "domains.dns.dnssec", sortKey: "dnssec" },
  { key: "email_enabled", labelKey: "domains.email_enabled", sortKey: "email_enabled" },
  // Renewal + resolved price (#250). The price is resolved per row (override → TLD list),
  // which the server cannot order by — an honest quiet header (docs/UX.md).
  {
    key: "next_invoice",
    labelKey: "domains.renewal",
    sortKey: "next_invoice_date",
    align: "right",
  },
  { key: "price", labelKey: "domains.price", align: "right" },
  // Resolved server-side from a three-state flag and the registrar registers (#298), so there
  // is nothing to sort by — a quiet header, like the price beside it.
  { key: "invoiceable", labelKey: "domains.invoiceable.column" },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];

export const TLD_PRICES_TABLE_ID = "domain-tld-prices";

/** The TLD price list (#250): one row per TLD, grouped client-side from the full (small)
 * price history, so every column is a quiet header — there is no server sort to mirror. */
export const TLD_PRICE_COLUMNS: ColumnMeta[] = [
  { key: "tld", labelKey: "domains.tld_prices.tld", primary: true, width: 140 },
  { key: "current", labelKey: "domains.tld_prices.current", defaultVisible: true, align: "right" },
  { key: "since", labelKey: "domains.tld_prices.since", defaultVisible: true, align: "right" },
  { key: "upcoming", labelKey: "domains.tld_prices.upcoming", defaultVisible: true },
  { key: "domains", labelKey: "domains.tld_prices.domains", defaultVisible: true, align: "right" },
];
