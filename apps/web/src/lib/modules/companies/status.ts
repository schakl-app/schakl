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
  lead: "bg-amber-100 text-amber-800",
  onboarding: "bg-sky-100 text-sky-700",
  active: "bg-green-100 text-green-700",
  offboarding: "bg-orange-100 text-orange-700",
  archived: "bg-neutral-100 text-neutral-500",
};

export function statusPillClass(status: string): string {
  return PILL[status] ?? "bg-neutral-100 text-neutral-600";
}
