/**
 * Shared vocabulary for the permission matrix (issue #19).
 *
 * A scoped permission is stored suffixed (`time.entry.write:own`); an unscoped one is stored
 * bare. The matrix therefore renders two different controls, and both encode into the same flat
 * list of strings that `PATCH /roles/{id}` expects.
 */
import type { components } from "$lib/core/api/schema";

export type PermissionRead = components["schemas"]["PermissionRead"];
export type RoleRead = components["schemas"]["RoleRead"];
export type PermissionCatalog = components["schemas"]["PermissionCatalog"];

/** `""` means the role does not hold it at all. */
export type Scope = "" | "own" | "any";

export const WILDCARD = "*";

/** Form field name for a scoped permission's three-way choice. */
export const scopeField = (key: string) => `scope:${key}`;

/** What the matrix shows as selected for `permission`, given the role's stored strings. */
export function currentScope(granted: readonly string[], key: string): Scope {
  if (granted.includes(`${key}:any`)) return "any";
  if (granted.includes(`${key}:own`)) return "own";
  return "";
}

export function holdsUnscoped(granted: readonly string[], key: string): boolean {
  return granted.includes(key);
}

/** Group the catalog for rendering, preserving the API's group order and per-group positions. */
export function groupPermissions(
  catalog: PermissionCatalog,
): { group: string; permissions: PermissionRead[] }[] {
  return catalog.groups.map((group) => ({
    group,
    permissions: catalog.permissions.filter((p) => p.group === group),
  }));
}

/**
 * The effective permission strings of a set of roles — the union, exactly as the API resolves it.
 * Used to show a user their effective set on the Gebruikers screen without an extra request per
 * member (docs/PERFORMANCE.md).
 */
export function effectivePermissions(roles: RoleRead[], roleIds: readonly string[]): string[] {
  const held = roles.filter((role) => roleIds.includes(role.id));
  if (held.some((role) => role.permissions.includes(WILDCARD))) return [WILDCARD];
  return [...new Set(held.flatMap((role) => role.permissions))].sort();
}
