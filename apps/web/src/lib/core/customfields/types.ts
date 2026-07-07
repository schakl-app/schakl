/**
 * Custom-fields client types + helpers (CLAUDE.md §13).
 *
 * Definitions are tenant data fetched from the API; labels live in `label_i18n` (per-locale),
 * not in the Paraglide catalogs. `fieldLabel` picks the label for the active locale.
 */
import type { components } from "$lib/core/api/schema";

export type CustomFieldDefinition = components["schemas"]["CustomFieldDefinitionRead"];
export type CustomFieldOption = components["schemas"]["CustomFieldOption"];

/** All v1 field types (mirrors the API `CustomFieldType`). */
export type CustomFieldType = CustomFieldDefinition["data_type"];

/** Pick a definition's label for the active locale, falling back to en, then the key. */
export function fieldLabel(def: CustomFieldDefinition, locale: string): string {
  const labels = (def.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.en || labels.nl || def.key;
}

/** Pick an option's label for the active locale, falling back to en, then the value. */
export function optionLabel(opt: CustomFieldOption, locale: string): string {
  const labels = (opt.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.en || labels.nl || opt.value;
}
