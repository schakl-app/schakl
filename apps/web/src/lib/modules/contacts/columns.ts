/**
 * The columns a contact-person list can show (#39).
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/contacts/service.py::SORTABLE`).
 *
 * **`companies` carries no `sortKey`**, though the API can order by it. The list is sectioned by
 * client, and a sort orders rows *within* a section and never reorders the sections
 * (docs/UX.md), so the header would visibly do nothing. The column itself stays: a person linked
 * to several clients is listed under each, and from inside one section its chips are the only
 * thing that says where else they appear — plus which of those clients they are the primary
 * contact for (`is_primary` marks the primary contact **for a company**, so one person can be
 * primary at several clients at once; there is no such thing as "their primary company").
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const CONTACTS_TABLE_ID = "contacts";

export const CONTACT_COLUMNS: ColumnMeta[] = [
  {
    key: "name",
    labelKey: "contacts.name",
    sortKey: "last_name",
    primary: true,
    width: 240,
  },
  { key: "companies", labelKey: "contacts.companies", defaultVisible: true },
  { key: "email", labelKey: "contacts.email", sortKey: "email", defaultVisible: true },
  { key: "phone", labelKey: "contacts.phone", defaultVisible: true },
  { key: "job_title", labelKey: "contacts.job_title", sortKey: "job_title" },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
