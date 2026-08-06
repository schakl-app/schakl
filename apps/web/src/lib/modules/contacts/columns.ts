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

// Every non-primary column states a `width`. The table lays out `table-fixed`, which shares the
// remainder equally between the columns that declare none — so a bare list would give the client
// chips and the created date the same share as the person's name. The primary column keeps its
// number for a dragged layout, but is the one that absorbs the slack (`DataTable.flexKey`), so
// these are what actually decide the shape: ~590px of fixed columns by default, name takes the rest.
export const CONTACT_COLUMNS: ColumnMeta[] = [
  {
    key: "name",
    labelKey: "contacts.name",
    sortKey: "last_name",
    primary: true,
    width: 240,
  },
  // Chips, one per client this person is linked to — the widest of the three default columns.
  { key: "companies", labelKey: "contacts.companies", defaultVisible: true, width: 220 },
  { key: "email", labelKey: "contacts.email", sortKey: "email", defaultVisible: true, width: 220 },
  { key: "phone", labelKey: "contacts.phone", defaultVisible: true, width: 150 },
  { key: "job_title", labelKey: "contacts.job_title", sortKey: "job_title", width: 170 },
  {
    key: "created_at",
    labelKey: "table.column.created_at",
    sortKey: "created_at",
    align: "right",
    width: 110,
  },
];
