/**
 * "Open this record in edit mode" hand-off (issue #78).
 *
 * The overview kebab (clients, contacts, projects) has no edit surface of its own — the form
 * lives on the detail page. So Edit there is a link to the detail page carrying `?edit=1`, and
 * the detail page reads it once, on mount, to open its existing edit affordance (a modal for a
 * client, the inline toggle for a contact/project). One source of truth per form; the overview
 * grows no second copy.
 *
 * The param name lives here, used by both sides, so the producer and consumer can never drift.
 * `editIntent()` is meant for a `$state(...)` initializer, not a `$derived`: it opens the surface
 * on load, then hands control to the user — who can close it without the URL forcing it back open.
 */
import { page } from "$app/state";

const EDIT_PARAM = "edit";

/** Append the edit-intent marker to a detail-page path, for an ActionsMenu `href`. */
export function editHref(path: string): string {
  return `${path}?${EDIT_PARAM}=1`;
}

/** True when this page was opened via an overview's Edit link. Read once, in a state initializer. */
export function editIntent(): boolean {
  return page.url.searchParams.get(EDIT_PARAM) === "1";
}
