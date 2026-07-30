/**
 * Effective permissions in the browser (issue #19).
 *
 * A mirror of the API's `PermissionSet.has` — and it must stay a mirror, or the UI will offer
 * a button the API then refuses. The subtlety is the same: a scoped permission is only ever
 * *stored* suffixed (`time.entry.write:own`), so a check with no scope means "holds this at
 * some scope", and `:any` satisfies a check for `:own`.
 *
 * **This is UX, not security.** Hiding a nav item is not a permission check; the API is the
 * boundary. Every gate expressed here is already enforced server-side.
 */

export const WILDCARD = "*";

export type PermissionScope = "own" | "any";

/** The only thing a permission check needs from a user. Keeps this module free of `session.ts`,
 * which pulls the API client in and must never reach the browser bundle. */
export interface PermissionHolder {
  permissions?: readonly string[];
}

/**
 * Does this user hold `key`? Mirrors the API's `PermissionSet.has` exactly, and must keep
 * mirroring it — otherwise the UI offers a button the API then refuses.
 */
export function can(
  user: PermissionHolder | null | undefined,
  key: string,
  scope?: PermissionScope,
): boolean {
  return hasPermission(user?.permissions, key, scope);
}

export function hasPermission(
  granted: readonly string[] | undefined,
  key: string,
  scope?: PermissionScope,
): boolean {
  if (!granted?.length) return false;
  if (granted.includes(WILDCARD)) return true; // owner
  if (granted.includes(key)) return true; // genuinely unscoped permissions
  if (scope === "any") return granted.includes(`${key}:any`);
  // scope is undefined (a route's floor) or "own": a broad grant satisfies a narrow ask.
  return granted.includes(`${key}:own`) || granted.includes(`${key}:any`);
}

/**
 * `canAccessSettings` used to live here, over a hand-kept list of "one permission per Instellingen
 * screen". It drifted: two entries named permissions no screen guards on, and eight screens were
 * missing entirely — so an admin holding only `settings.nav.manage` could not reach Instellingen.
 * It now derives from the screen registry (`core/settings-nav.ts`), which the index grid, the
 * section rail and the breadcrumb labels read as well.
 */
export { canAccessSettings } from "./settings-nav";
