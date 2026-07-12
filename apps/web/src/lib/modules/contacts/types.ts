/** Tenant-configurable contact types (issue #91) — the label-resolution helper. */
import type { components } from "$lib/core/api/schema";

export type ContactType = components["schemas"]["ContactTypeRead"];

/** Resolve a contact type's display label for the active locale (tenant data, not a message key). */
export function contactTypeLabel(
  type: Pick<ContactType, "label_i18n" | "key"> | undefined | null,
  locale: string,
): string {
  if (!type) return "";
  const labels = (type.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.nl || labels.en || type.key;
}
