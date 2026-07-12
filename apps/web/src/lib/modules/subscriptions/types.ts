/** Tenant-configurable subscription types + templates (issue #142) — label helpers. */
import type { components } from "$lib/core/api/schema";

export type SubscriptionType = components["schemas"]["SubscriptionTypeRead"];
export type SubscriptionTemplate = components["schemas"]["SubscriptionTemplateRead"];

/** Resolve a subscription type's display label for the active locale (tenant data, not a message key). */
export function subscriptionTypeLabel(
  type: Pick<SubscriptionType, "label_i18n" | "key"> | undefined | null,
  locale: string,
): string {
  if (!type) return "";
  const labels = (type.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.nl || labels.en || type.key;
}
