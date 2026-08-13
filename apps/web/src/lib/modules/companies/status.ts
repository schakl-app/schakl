/** Client lifecycle statuses (mirrors the API's CompanyStatus) + their pill styling. */

export const COMPANY_STATUSES = [
  "lead",
  "onboarding",
  "active",
  "offboarding",
  "archived",
] as const;

export type CompanyStatus = (typeof COMPANY_STATUSES)[number];

/**
 * The working book of business: every status except the archive (#329).
 *
 * Derived rather than written out, so a sixth status joins the default view by being added
 * above — only the archive is named here, because only the archive is the thing being left out.
 * Sent to the API as its comma-separated `status`; the API's own default is still *everything*,
 * because the pickers, the impex export and the generated MCP surface all call that endpoint and
 * narrowing it would change what they are told the org's clients are. The screen picks its
 * default, the API only makes it expressible.
 */
export const COMPANY_WORKING_SET = COMPANY_STATUSES.filter((s) => s !== "archived").join(",");

/**
 * The URL token for "everything, archive included".
 *
 * Once "no `?status=`" means the working set, that state needs a name of its own, or it is a
 * view the user can reach and cannot link to, bookmark or come back to with the back button
 * (§9, the URL is the view). Web-side only: it resolves to *no* `status` on the wire.
 */
export const COMPANY_STATUS_ALL = "all";

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
