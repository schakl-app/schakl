/** Client lifecycle statuses (mirrors the API's CompanyStatus) + their pill styling. */

export const COMPANY_STATUSES = [
  "lead",
  "onboarding",
  "active",
  "offboarding",
  "archived",
] as const;

export type CompanyStatus = (typeof COMPANY_STATUSES)[number];

const PILL: Record<string, string> = {
  lead: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  onboarding: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  active: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  offboarding: "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  archived: "bg-surface text-text-muted",
};

export function statusPillClass(status: string): string {
  return PILL[status] ?? "bg-surface text-text-muted";
}
