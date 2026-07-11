/**
 * Rendering a record's activity line (issue #67).
 *
 * The API ships an `action` and a payload of raw before/after values, never a translated
 * string — the reader's locale builds the sentence, so the panel reads alike everywhere. This
 * is the one place that turns the pair into text.
 *
 * A field's value is rendered for what it is: a status is a token in the entity's own
 * vocabulary, a date prints European (never an ISO string), a boolean is ja/nee. A field that
 * is a foreign key to a person or a client (`responsible_user_id`, `company_id`) shows only its
 * label — a raw UUID is worse than saying nothing — so the trail reads "changed Verantwoordelijke"
 * rather than leaking an id. The full before/after values still live in the stored row.
 */
import { fmtDayMonth } from "$lib/core/format";
import { t } from "$lib/core/i18n";

export interface ActivityLike {
  action: string;
  entity_type: string;
  payload?: Record<string, unknown>;
}

interface Change {
  from: unknown;
  to: unknown;
}

/** Each entity type keeps its status vocabulary in its own namespace (mirrors notifications). */
const STATUS_NAMESPACE: Record<string, string> = {
  task: "tasks.status",
  project: "projects.status",
  company: "companies.status",
};

/** FK-to-a-record fields: show the label, never the raw id behind it. */
const LABEL_ONLY_FIELDS = new Set(["responsible_user_id", "company_id"]);

/** Date-only fields, printed as a European day. */
const DATE_FIELDS = new Set(["start_date", "end_date", "due_date"]);

function fieldLabel(field: string): string {
  return t(`activity.field.${field}`);
}

function renderValue(entityType: string, field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return t("activity.value.empty");
  if (field === "status" && typeof value === "string") {
    const namespace = STATUS_NAMESPACE[entityType];
    return namespace ? t(`${namespace}.${value}`) : value;
  }
  if (DATE_FIELDS.has(field) && typeof value === "string") return fmtDayMonth(value);
  if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
  return String(value);
}

function changeText(entityType: string, field: string, change: Change): string {
  const label = fieldLabel(field);
  if (LABEL_ONLY_FIELDS.has(field)) return label;
  return t("activity.change", {
    field: label,
    from: renderValue(entityType, field, change.from),
    to: renderValue(entityType, field, change.to),
  });
}

/** The sentence an activity entry reads as, after the actor's name, in the reader's locale. */
export function activityText(item: ActivityLike): string {
  if (item.action === "updated") {
    const changes = (item.payload?.changes ?? {}) as Record<string, Change>;
    const parts = Object.entries(changes).map(([field, change]) =>
      changeText(item.entity_type, field, change),
    );
    return t("activity.action.updated", { changes: parts.join(", ") });
  }
  // `created` today; an unknown action falls back to its own key rather than throwing.
  return t(`activity.action.${item.action}`);
}
