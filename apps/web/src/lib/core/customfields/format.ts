/**
 * Render one custom value for display (CLAUDE.md §13).
 *
 * Extracted from `CustomFieldsView` so a table cell and a detail row show the same thing, and
 * fixing the gap it had: `select` / `multi_select` were printing the *stored value* rather than
 * the tenant's localized option label, so a field whose options are `{value: "nl", label: "Nederland"}`
 * rendered as "nl".
 */
import { fmtNumericDate } from "$lib/core/format";

import type { CustomFieldDefinition } from "./types";
import { optionLabel } from "./types";

export const EMPTY = "—";

function labelFor(def: CustomFieldDefinition, value: unknown, locale: string): string {
  const option = (def.options_json ?? []).find((o) => o.value === value);
  return option ? optionLabel(option, locale) : String(value);
}

/** A custom value as text. Never throws on a value whose definition changed type underneath it. */
export function formatCustomValue(
  def: CustomFieldDefinition,
  value: unknown,
  locale: string,
): string {
  if (value === null || value === undefined || value === "") return EMPTY;

  switch (def.data_type) {
    case "boolean":
      return value ? "✓" : EMPTY;
    case "select":
      return labelFor(def, value, locale);
    case "multi_select":
      if (!Array.isArray(value)) return labelFor(def, value, locale);
      return value.length ? value.map((v) => labelFor(def, v, locale)).join(", ") : EMPTY;
    case "date":
    case "datetime":
      return typeof value === "string" ? fmtNumericDate(value.slice(0, 10)) : String(value);
    default:
      // A definition retyped after the fact can leave an array or object behind.
      if (Array.isArray(value)) return value.length ? value.join(", ") : EMPTY;
      return String(value);
  }
}

/** Numbers align right in a table; everything else reads left. */
export function customFieldAlign(def: CustomFieldDefinition): "left" | "right" {
  return def.data_type === "number" ? "right" : "left";
}
