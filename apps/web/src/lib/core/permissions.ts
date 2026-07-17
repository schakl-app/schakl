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
 * One permission per Instellingen screen. The landing page and the sidebar link show iff the
 * user can reach at least one of them — an agency may hand someone `settings.branding.write`
 * and nothing else, and Instellingen must then still be findable.
 */
export const SETTINGS_SCREEN_PERMISSIONS = [
  "settings.roles.manage",
  "companies.group.manage",
  "settings.branding.write",
  "settings.auth.manage",
  "settings.customfields.write",
  "settings.dashboard.manage",
  "settings.system.read",
  "members.member.read",
  "tasks.label.write",
  "leave.type.write",
  "notifications.defaults.manage",
  "settings.providers.manage",
  "contacts.type.manage",
  "subscriptions.type.manage",
  "invoicing.settings.manage",
  "automation.rule.read",
  "ai.settings.manage",
  "settings.service_access.manage",
] as const;

export function canAccessSettings(granted: readonly string[] | undefined): boolean {
  return SETTINGS_SCREEN_PERMISSIONS.some((key) => hasPermission(granted, key));
}
