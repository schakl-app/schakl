/** Tenant-configurable subscription types + templates (issue #142) — label helpers. */
import type { components } from "$lib/core/api/schema";

import type { CustomFieldDefinition } from "$lib/core/customfields/types";
import type { AutoInvoiceMode } from "$lib/modules/invoicing/types";

export type SubscriptionType = components["schemas"]["SubscriptionTypeRead"];
export type SubscriptionTemplate = components["schemas"]["SubscriptionTemplateRead"];
export type Subscription = components["schemas"]["SubscriptionRead"];

/**
 * Everything the agreement form's pickers draw from, gathered once by whoever mounts it: the
 * list page's layout load, or a client page's dialog the moment it is opened.
 */
export interface SubscriptionFormLookups {
  companies: { id: string; name: string; status?: string | null }[];
  /** With the client each project belongs to: the links picker narrows to the agreement's. */
  projects: { id: string; name: string; status?: string | null; company_id?: string | null }[];
  types: SubscriptionType[];
  templates: SubscriptionTemplate[];
  /** The tenant's own subscription fields. */
  definitions: CustomFieldDefinition[];
  /** The company ones, for the inline client quick-create. */
  companyDefinitions: CustomFieldDefinition[];
  /** Names the inherited level in the "follow the organisation" hint; `null` when unreadable. */
  orgAutoInvoiceMode: AutoInvoiceMode | null;
}

/** Resolve a subscription type's display label for the active locale (tenant data, not a message key). */
export function subscriptionTypeLabel(
  type: Pick<SubscriptionType, "label_i18n" | "key"> | undefined | null,
  locale: string,
): string {
  if (!type) return "";
  const labels = (type.label_i18n ?? {}) as Record<string, string>;
  return labels[locale] || labels.nl || labels.en || type.key;
}
