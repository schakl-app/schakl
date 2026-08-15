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
import { fmtDayMonth, fmtMoney, fmtNumericDate } from "$lib/core/format";
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
const LABEL_ONLY_FIELDS = new Set([
  "responsible_user_id",
  "company_id",
  "hosting_id",
  // A monitor being attached to what it watches (#321). Without these the trail prints the raw
  // UUID at the reader — the "its input was a database row" mistake, in a sentence a person
  // is meant to read.
  "website_id",
  "domain_id",
]);

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

/** A stored amount, printed as money; anything unparseable prints as the empty marker. */
function money(value: unknown): string {
  const amount = Number(value);
  if (value === null || value === undefined || value === "" || Number.isNaN(amount)) {
    return t("activity.value.empty");
  }
  return fmtMoney(amount);
}

/** A stored date-only value, printed European. Guarded: `Intl` throws on an invalid Date. */
function isoDate(value: unknown): string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? fmtNumericDate(value)
    : t("activity.value.empty");
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
  if (
    item.action === "file_attached" ||
    item.action === "file_removed" ||
    item.action === "logo_uploaded"
  ) {
    return t(`activity.action.${item.action}`, {
      filename: String(item.payload?.filename ?? ""),
    });
  }
  // A price change written by a (possibly bulk) price increase (#231). The amounts are stored
  // raw, so the *reader's* locale formats them, like every other money cell.
  if (item.action === "price_increased") {
    return t("activity.action.price_increased", {
      from: money(item.payload?.from),
      to: money(item.payload?.to),
      valid_from: isoDate(item.payload?.valid_from),
    });
  }
  // Two contactmomenten tied into one conversation (#272). Handled before the prefix branch
  // below: its payload is a pointer, not the kind/subject those messages interpolate.
  if (item.action === "interaction.conversation_linked") {
    return t("activity.action.interaction.conversation_linked");
  }
  // A contactmoment milestone mirrored onto its host record (#152): logged / linked / unlinked.
  if (item.action.startsWith("interaction.")) {
    return t(`activity.action.${item.action}`, {
      kind: t(`interactions.kind.${String(item.payload?.kind ?? "note")}`),
      subject: String(item.payload?.subject ?? ""),
    });
  }
  // A marketing account linked/unlinked on the client (#134).
  if (item.action.startsWith("marketing.")) {
    return t(`activity.action.${item.action}`, {
      source: t(`marketing.source.${String(item.payload?.source ?? "")}`),
      name: String(item.payload?.name ?? ""),
    });
  }
  // Everything else reads its own key **with the payload as ICU params**: a recorded action's
  // payload keys are exactly its message's placeholders (`{email}`, `{title}`, `{count}`), so a
  // module can add an action without touching this file. An unknown action still falls back to
  // its key rather than throwing.
  return t(`activity.action.${item.action}`, presentPayload(item.payload));
}

/**
 * Money and dates in a payload print like money and dates (#357).
 *
 * `price_increased` already ran its two amounts through `money()`; `payment_registered` and
 * `payment_deleted` — which arrive on the generic path — did not, so the invoice trail read
 * "een betaling van -1164.02" inside a card whose every other number said "€ -1.164,02". Naming
 * the *keys* rather than the actions is what stops the next module recording a sum from
 * repeating it: a value is formatted for what its key says it is, not for which branch happened
 * to catch it.
 */
const MONEY_PAYLOAD_KEYS = new Set(["amount", "price", "total", "subtotal", "unit_price"]);

function presentPayload(payload: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!payload) return {};
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (MONEY_PAYLOAD_KEYS.has(key) && value !== null && value !== undefined && value !== "") {
      out[key] = money(value);
    } else if (key.endsWith("_date") && typeof value === "string") {
      out[key] = isoDate(value);
    } else {
      out[key] = value;
    }
  }
  return out;
}
