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

/**
 * The work the agency still has open: every status except the archive.
 *
 * Derived rather than written out, so a fifth status joins the default view by being added above
 * — only the archive is named here, because only the archive is the thing being left out. Sent to
 * the API as its comma-separated `status`; the API's own default is still *everything*, because
 * the pickers, the impex export and the generated MCP surface all call that endpoint and
 * narrowing it would change what they are told the org's projects are. The screen picks its
 * default, the API only makes it expressible.
 *
 * Deliberately wider than the *picker's* rule (`picker.ts` retires `completed` as well): a list
 * is where you go to find a project, and one delivered last month is among the likelier things to
 * be looking for. A picker is a suggestion, and that one is not.
 */
export const PROJECT_WORKING_SET = PROJECT_STATUSES.filter((s) => s !== "archived").join(",");

/**
 * The URL token for "everything, archive included".
 *
 * Once "no `?status=`" means the working set, that state needs a name of its own, or it is a view
 * the user can reach and cannot link to, bookmark or come back to with the back button (§9, the
 * URL is the view). Web-side only: it resolves to *no* `status` on the wire.
 */
export const PROJECT_STATUS_ALL = "all";

const PILL: Record<string, string> = {
  active: "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  on_hold: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  completed: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  archived: "bg-surface text-text-muted",
};

export function statusPillClass(status: string): string {
  return PILL[status] ?? "bg-surface text-text-muted";
}
