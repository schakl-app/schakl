/**
 * The columns a contact-person list can show (#39).
 *
 * `sortKey` mirrors the API's allow-list (`apps/api/app/modules/contacts/service.py::SORTABLE`).
 * `company` sorts by the alphabetically first client the contact is linked to — *not* by "their
 * primary company", which does not exist: `is_primary` marks the primary contact **for a
 * company**, so one person can be primary at several clients at once.
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
  { key: "companies", labelKey: "contacts.companies", sortKey: "company", defaultVisible: true },
  { key: "email", labelKey: "contacts.email", sortKey: "email", defaultVisible: true },
  { key: "phone", labelKey: "contacts.phone", defaultVisible: true },
  { key: "job_title", labelKey: "contacts.job_title", sortKey: "job_title" },
  { key: "created_at", labelKey: "table.column.created_at", sortKey: "created_at", align: "right" },
];
