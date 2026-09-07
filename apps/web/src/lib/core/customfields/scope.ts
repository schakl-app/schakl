/**
 * A custom field that applies to *some* rows of its entity type (CLAUDE.md §13) — the browser's
 * copy of `app/core/customfields/scoping.py`, so the form draws exactly the fields the API will
 * hold the row to.
 *
 * A definition's `config_json.scope` is `{ <dimension key>: [values] }`, where a dimension key
 * is the entity attribute the row is judged on (`subscription_type_id`). It applies to a row
 * when it has no scope, or when for **any** dimension the row's value is in that list. A row
 * value of `null` never matches; `rowScope` itself being absent means everything applies.
 */
import type { CustomFieldDefinition } from "./types";

export const SCOPE_KEY = "scope";

/** The row's value per dimension key. */
export type RowScope = Record<string, string | null | undefined>;

/** The definition's scope, normalised; `{}` for an unscoped one. Junk-tolerant, as the API is. */
export function definitionScope(
  def: Pick<CustomFieldDefinition, "config_json">,
): Record<string, string[]> {
  const raw = (def.config_json as Record<string, unknown> | null | undefined)?.[SCOPE_KEY];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(raw as Record<string, unknown>)) {
    const list = typeof values === "string" ? [values] : Array.isArray(values) ? values : null;
    if (!list) continue;
    const cleaned = [...new Set(list.filter((v) => v != null && String(v)).map(String))];
    if (cleaned.length) out[key] = cleaned;
  }
  return out;
}

export function isScoped(def: Pick<CustomFieldDefinition, "config_json">): boolean {
  return Object.keys(definitionScope(def)).length > 0;
}

export function appliesTo(
  def: Pick<CustomFieldDefinition, "config_json">,
  rowScope: RowScope | null | undefined,
): boolean {
  const scope = definitionScope(def);
  if (!rowScope || Object.keys(scope).length === 0) return true;
  return Object.entries(scope).some(([key, values]) => {
    const current = rowScope[key];
    return current != null && values.includes(String(current));
  });
}

export function applicableDefinitions<T extends Pick<CustomFieldDefinition, "config_json">>(
  definitions: T[],
  rowScope: RowScope | null | undefined,
): T[] {
  return definitions.filter((def) => appliesTo(def, rowScope));
}
