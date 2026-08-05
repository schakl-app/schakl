/**
 * Project lifecycle statuses (mirrors the API's `ProjectStatus`), the way clients already have
 * one (`modules/companies/status.ts`).
 *
 * It exists because the vocabulary had started to be copied: the detail page's edit form spelled
 * it out, and the list's bulk dialog needed the same four words. Two copies of an enum drift the
 * day someone adds a fifth status to the API — and the one that drifts is the one nobody was
 * looking at.
 */

export const PROJECT_STATUSES = ["active", "on_hold", "completed", "archived"] as const;

export type ProjectStatus = (typeof PROJECT_STATUSES)[number];
