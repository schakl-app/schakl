/**
 * The columns the notifications list can show (#16, #24).
 *
 * `sortKey` mirrors the API's allow-list (`app/modules/notifications/service.py::SORTABLE`), which
 * holds exactly one key: an inbox is a chronology. The other columns therefore carry **no**
 * `sortKey` and their headers stay quiet — a header that claims to sort and doesn't is worse than
 * a quiet one (docs/UX.md). The message is the primary column, so it carries the row's link out.
 */
import type { ColumnMeta } from "$lib/core/table/columns";

export const NOTIFICATIONS_TABLE_ID = "notifications";

/** The entity types the API will filter by; anything else is not a filter we offer. */
export const ENTITY_TYPES = ["task", "project", "company", "leave_request", "timesheet"] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export function asEntityType(value: string | null): EntityType | undefined {
  return ENTITY_TYPES.includes(value as EntityType) ? (value as EntityType) : undefined;
}

export const NOTIFICATION_COLUMNS: ColumnMeta[] = [
  {
    key: "message",
    labelKey: "notifications.column.message",
    primary: true,
    width: 460,
  },
  { key: "record", labelKey: "notifications.column.record", defaultVisible: true, width: 200 },
  { key: "actor", labelKey: "notifications.column.actor", defaultVisible: true, width: 180 },
  {
    key: "when",
    labelKey: "notifications.column.when",
    sortKey: "created_at",
    defaultVisible: true,
    width: 150,
  },
];
