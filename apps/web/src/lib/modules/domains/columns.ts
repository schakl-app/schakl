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
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
